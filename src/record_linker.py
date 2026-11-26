"""Cross-Script Record Linker & Identity Resolution Engine."""
from __future__ import annotations

import csv
import hashlib
import hmac
import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from indic_transliteration import sanscript
import rapidfuzz
from src.ingestion import detect_and_decode

MRN_RE = re.compile(
    r"\b(?:SNJ|JJH|ADM|IPD|DHH|IP|UHID|OPD|CR|REG)[-_/]?\d{2,6}[-_/]?\d{2,8}\b|"
    r"\b[A-Z]{2,5}/\d{2}/\d{3,8}\b|"
    r"\b(?:UHID|MRN|IPD|REG\s*NO|CR\s*NO|OPD\s*NO|IP\s*NO|PATIENT\s*ID)[-:\s]*\d+[A-Z0-9-]*\b",
    re.IGNORECASE
)
PHONE_RE = re.compile(r"(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}(?!\d)|(?<!\d)0[6-9]\d{4}[\s-]?\d{5}(?!\d)|(?<!\d)[6-9]\d{9}(?!\d)")
AADHAAR_RE = re.compile(r"(?<!\d)\d{12}(?!\d)")


def extract_phone(s: str) -> str | None:
    """Extract standard 10-digit Indian mobile number stripping country codes and delimiters."""
    if not s:
        return None
    digits = re.sub(r"[^\d]", "", str(s))
    if len(digits) == 12 and digits.startswith("91") and digits[2] in "6789":
        return digits[2:]
    if len(digits) == 11 and digits.startswith("0") and digits[1] in "6789":
        return digits[1:]
    if len(digits) == 10 and digits[0] in "6789":
        return digits
    m = re.search(r"[6-9]\d{9}", digits)
    if m:
        return m.group(0)
    return None


def norm_id(val: str) -> str:
    """Normalize identifier by stripping non-alphanumeric characters."""
    if not val:
        return ""
    return re.sub(r"[-_/\s]", "", str(val)).lower()


DEVA_DIGITS_TO_ASCII = str.maketrans("०१२३४५६७८९", "0123456789")


def clean_itrans(s: str) -> str:
    """Devanagari cross-script transliteration and Indian name canonicalization."""
    if not s:
        return ""
    # Check if string contains Devanagari characters
    if any("\u0900" <= ch <= "\u097F" for ch in s):
        s = sanscript.transliterate(s, sanscript.DEVANAGARI, sanscript.ITRANS).lower()

    s = s.lower().replace("ph", "f").replace("sh", "s").replace("ee", "i").replace("oo", "u").replace("q", "k").replace("ck", "k").replace(".", " ")
    tokens = s.split()
    res = []
    for t in tokens:
        if t in ("md", "mohd", "mohammed", "muhammad"):
            res.append("mohammad")
        elif t in ("so", "wo", "do", "co", "s/o", "w/o", "d/o", "c/o", "sahab", "saheb", "ji", "bhai", "babu", "didi", "smt", "shri", "kumari", "pt", "mr", "mrs", "ms"):
            continue
        else:
            if t.endswith("a") and len(t) > 2:
                t = t[:-1]
            res.append(t)
    return " ".join(res)


@dataclass
class PatientCandidate:
    names: set[str] = field(default_factory=set)
    raw_names: set[str] = field(default_factory=set)
    mrns: set[str] = field(default_factory=set)
    phones: set[str] = field(default_factory=set)
    aadhaars: set[str] = field(default_factory=set)
    relatives: set[str] = field(default_factory=set)
    villages: set[str] = field(default_factory=set)
    gender: str = ""
    age: int | None = None


class RecordLinker:
    """Extracts candidate features and links records into stable patient clusters."""

    @classmethod
    def extract_candidates(cls, inp: Path) -> dict[tuple[str, str], PatientCandidate]:
        candidates: dict[tuple[str, str], PatientCandidate] = {}

        for p in sorted(inp.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(inp).as_posix()
            ext = p.suffix.lower()
            raw = p.read_bytes()

            txt, _ = detect_and_decode(raw, ext=ext)
            if not txt:
                continue
            txt = txt.translate(DEVA_DIGITS_TO_ASCII)

            # 1. Tabular CSV/TSV
            if ext in (".csv", ".tsv"):
                delim = "\t" if ext == ".tsv" else ","
                try:
                    reader = list(csv.reader(txt.splitlines(), delimiter=delim))
                    if not reader:
                        continue
                    header = [re.sub(r"[-_\s]", "", h.strip().lower()) for h in reader[0]]
                    raw_header = [h.strip() for h in reader[0]]
                    for i, row in enumerate(reader[1:]):
                        cand = PatientCandidate()
                        name_parts = []
                        for j, cell in enumerate(row):
                            if j >= len(header):
                                continue
                            h = header[j]
                            h_raw = raw_header[j]
                            c = cell.strip()
                            if not c:
                                continue
                            if h in ("firstname", "lastname"):
                                name_parts.append(c)
                            elif (any(k in h for k in ("patientname", "beneficiaryname", "fullname", "patientfullname", "patname", "naam")) or
                                  "मरीज" in h_raw or "रोगी" in h_raw or h in ("name", "patient", "pt")):
                                cand.raw_names.add(c)
                                cand.names.add(clean_itrans(c))
                            elif (any(k in h for k in ("uhid", "mrn", "patientuid", "caseid", "hospitaluid", "patientid", "legacymrn", "mrncode", "regno", "ipd", "opd", "ipno", "opno", "crno", "admissionno", "altid")) or
                                  "पंजीकरण" in h_raw or "आईडी" in h_raw):
                                cand.mrns.add(norm_id(c))
                            elif any(k in h for k in ("phone", "mobile", "contact", "callback", "cell", "mob")) or "मोबाइल" in h_raw:
                                ph = extract_phone(c)
                                if ph: cand.phones.add(ph)
                            elif any(k in h for k in ("aadhaar", "aadhar", "maskeduid")):
                                m = AADHAAR_RE.search(c.replace(" ", "").replace("-", ""))
                                if m: cand.aadhaars.add(m.group(0))
                            elif (any(k in h for k in ("guardian", "relative", "caregiver", "careof", "father", "husband", "spouse", "mother", "pita", "pati", "mata")) or
                                  "पिता" in h_raw or "पति" in h_raw or "माता" in h_raw):
                                cand.relatives.add(clean_itrans(c))
                            elif any(k in h for k in ("villagetown", "village", "city", "town", "gaon", "gram", "post", "po")) or "ग्राम" in h_raw:
                                cand.villages.add(clean_itrans(c))
                            elif h in ("gender", "sex", "ling"):
                                cand.gender = c[0].upper() if c else ""
                        if name_parts:
                            full_n = " ".join(name_parts)
                            cand.raw_names.add(full_n)
                            cand.names.add(clean_itrans(full_n))
                        candidates[(rel, f"row:{i}")] = cand
                except Exception:
                    pass

            # 2. JSON files
            elif ext == ".json":
                try:
                    data = json.loads(txt)
                    if isinstance(data, dict) and "patients" in data and isinstance(data["patients"], list):
                        for i, pat in enumerate(data.get("patients", [])):
                            cand = PatientCandidate(gender=pat.get("gender", "")[:1].upper() if isinstance(pat.get("gender"), str) else "")
                            p_id = pat.get("uhid") or pat.get("mrn") or pat.get("patient_id")
                            if p_id: cand.mrns.add(norm_id(p_id))
                            
                            p_name = pat.get("name")
                            if isinstance(p_name, dict):
                                full_n = " ".join(filter(None, [p_name.get("first"), p_name.get("last")]))
                                if full_n:
                                    cand.raw_names.add(full_n)
                                    cand.names.add(clean_itrans(full_n))
                                if p_name.get("local"):
                                    cand.raw_names.add(p_name["local"])
                                    cand.names.add(clean_itrans(p_name["local"]))
                            elif isinstance(p_name, str) and p_name:
                                cand.raw_names.add(p_name)
                                cand.names.add(clean_itrans(p_name))
                                
                            p_phone = pat.get("contact", {}).get("phone") or pat.get("contact", {}).get("mobile") or pat.get("phone") or pat.get("mobile")
                            if p_phone:
                                ph = extract_phone(str(p_phone))
                                if ph: cand.phones.add(ph)
                            aadh = pat.get("ids", {}).get("aadhar_no") or pat.get("ids", {}).get("aadhaar") or pat.get("aadhaar") or pat.get("aadhar")
                            if aadh: cand.aadhaars.add(str(aadh))
                            g_name = pat.get("guardian", {}).get("name") or pat.get("relative_name") or pat.get("father_name")
                            if g_name: cand.relatives.add(clean_itrans(str(g_name)))
                            v_name = pat.get("address", {}).get("village") or pat.get("address", {}).get("city")
                            if v_name: cand.villages.add(clean_itrans(str(v_name)))
                            candidates[(rel, f"/patients/{i}")] = cand

                    elif isinstance(data, dict) and "visits" in data and isinstance(data["visits"], list):
                        for i, v in enumerate(data.get("visits", [])):
                            cand = PatientCandidate()
                            v_id = v.get("patient_ref") or v.get("uhid") or v.get("mrn")
                            if v_id: cand.mrns.add(norm_id(v_id))
                            pinfo = v.get("patient", {})
                            if isinstance(pinfo, dict):
                                if pinfo.get("name"):
                                    cand.raw_names.add(pinfo["name"])
                                    cand.names.add(clean_itrans(pinfo["name"]))
                                if pinfo.get("mobile") or pinfo.get("phone"):
                                    ph = extract_phone(str(pinfo.get("mobile") or pinfo.get("phone")))
                                    if ph: cand.phones.add(ph)
                            candidates[(rel, f"/visits/{i}")] = cand

                    elif isinstance(data, dict) and "results" in data and isinstance(data["results"], list) and any("uhid" in r or "patient_name" in r or "patient" in r for r in data["results"][:3]):
                        for i, r in enumerate(data.get("results", [])):
                            cand = PatientCandidate()
                            r_id = r.get("uhid") or r.get("mrn")
                            if r_id: cand.mrns.add(norm_id(r_id))
                            if r.get("patient_name"):
                                cand.raw_names.add(r["patient_name"])
                                cand.names.add(clean_itrans(r["patient_name"]))
                            candidates[(rel, f"/results/{i}")] = cand

                    elif isinstance(data, dict) and "patient" in data and isinstance(data["patient"], dict):
                        pinfo = data["patient"]
                        cand = PatientCandidate(gender=pinfo.get("sex", "")[:1].upper() if isinstance(pinfo.get("sex"), str) else "")
                        p_id = pinfo.get("hospital_uhid") or pinfo.get("uhid") or pinfo.get("mrn")
                        if p_id: cand.mrns.add(norm_id(p_id))
                        if pinfo.get("name"):
                            cand.raw_names.add(pinfo["name"])
                            cand.names.add(clean_itrans(pinfo["name"]))
                        if pinfo.get("phone") or pinfo.get("mobile"):
                            ph = extract_phone(str(pinfo.get("phone") or pinfo.get("mobile")))
                            if ph: cand.phones.add(ph)
                        candidates[(rel, "doc")] = cand

                    elif isinstance(data, dict) and (data.get("resourceType") == "Bundle" or "entry" in data):
                        for entry in data.get("entry", []):
                            res = entry.get("resource", {})
                            if res.get("resourceType") == "Patient":
                                cand = PatientCandidate(gender=res.get("gender", "")[:1].upper() if isinstance(res.get("gender"), str) else "")
                                for ident in res.get("identifier", []): cand.mrns.add(norm_id(ident.get("value", "")))
                                for n in res.get("name", []):
                                    if n.get("text"):
                                        cand.raw_names.add(n["text"])
                                        cand.names.add(clean_itrans(n["text"]))
                                for tel in res.get("telecom", []):
                                    ph = extract_phone(str(tel.get("value", "")))
                                    if ph: cand.phones.add(ph)
                                for addr in res.get("address", []):
                                    if addr.get("city"): cand.villages.add(clean_itrans(addr["city"]))
                                for cont in res.get("contact", []):
                                    if cont.get("name", {}).get("text"): cand.relatives.add(clean_itrans(cont["name"]["text"]))
                                candidates[(rel, "doc")] = cand
                    else:
                        cand = PatientCandidate()
                        # Extract from filename
                        fn_stem = Path(rel).stem
                        ph_fn = extract_phone(fn_stem)
                        if ph_fn: cand.phones.add(ph_fn)
                        for m in MRN_RE.finditer(fn_stem): cand.mrns.add(norm_id(m.group(0)))
                        m_fn = re.search(r"([A-Z][a-z]+_[A-Z][a-z]+)", fn_stem)
                        if m_fn:
                            raw_n = m_fn.group(1).replace("_", " ")
                            cand.raw_names.add(raw_n)
                            cand.names.add(clean_itrans(raw_n))
                        # Extract from JSON content
                        for m in PHONE_RE.finditer(txt):
                            ph = extract_phone(m.group(0))
                            if ph: cand.phones.add(ph)
                        for m in MRN_RE.finditer(txt): cand.mrns.add(norm_id(m.group(0)))
                        if cand.names or cand.mrns or cand.phones:
                            candidates[(rel, "doc")] = cand
                except Exception:
                    pass

            # 3. Free text notes & discharge summaries
            elif ext == ".txt":
                cand = PatientCandidate()
                for m in MRN_RE.finditer(txt): cand.mrns.add(norm_id(m.group(0)))
                for m in PHONE_RE.finditer(txt):
                    ph = extract_phone(m.group(0))
                    if ph: cand.phones.add(ph)
                for line in txt.splitlines():
                    line_s = line.strip()
                    if any(line_s.startswith(pfx) for pfx in (
                        "Name:", "Patient Name:", "Patient:", "Pt.", "नाम:",
                        "मरीज़ का नाम:", "मरीज़ का नाम :", "मरीज का नाम:", "मरीज का नाम :",
                        "रोगी का नाम:", "रोगी का नाम :", "रोगी:"
                    )):
                        val = line_s.split(":", 1)[1].split(";")[0].split(",")[0].split("|")[0].strip()
                        val = re.split(r"\bAge|\bआयु|\bReg|\bUHID", val)[0].strip()
                        if val:
                            cand.raw_names.add(val)
                            cand.names.add(clean_itrans(val))
                    if any(line_s.startswith(pfx) for pfx in ("Gram:", "ग्राम:", "Village:")):
                        val = line_s.split(":", 1)[1].strip()
                        if val: cand.villages.add(clean_itrans(val))
                    rel_m = re.search(r"(?:S/o|D/o|W/o|C/o|पिता/पति|पिता|पति)\s*(?:का\s*नाम)?\s*[:\-]?\s*([A-Za-z\u0900-\u097F]+(?:\s+[A-Za-z\u0900-\u097F]+)?)", line_s)
                    if rel_m: cand.relatives.add(clean_itrans(rel_m.group(1)))
                    hon_m = re.search(r"\b([A-Za-z\u0900-\u097F]+(?:\s+[A-Za-z\u0900-\u097F]+)?)\s+(?:sahab|saheb|ji|didi)\b", line_s)
                    if hon_m and not any(pfx in line_s for pfx in ("Dr.", "Doctor", "डॉ.")):
                        h_name = hon_m.group(1).strip()
                        cand.raw_names.add(h_name)
                        cand.names.add(clean_itrans(h_name))
                if "Discharge_Summary" in rel:
                    m = re.search(r"Discharge_Summary_([A-Za-z]+_[A-Za-z]+)", rel)
                    if m:
                        raw_n = m.group(1).replace("_", " ")
                        cand.raw_names.add(raw_n)
                        cand.names.add(clean_itrans(raw_n))
                if cand.names or cand.mrns or cand.phones:
                    candidates[(rel, "doc")] = cand

        return candidates

    @classmethod
    def link_candidates(
        cls,
        candidates: dict[tuple[str, str], PatientCandidate],
        secret: bytes
    ) -> tuple[dict[tuple[str, str], str | None], dict[tuple[str, str], set[str]]]:
        """Clusters candidates with deterministic hard ID merging and strict multi-signal validation."""
        keys = list(candidates.keys())
        parent = {i: i for i in range(len(keys))}

        def find(i: int) -> int:
            if parent[i] == i:
                return i
            parent[i] = find(parent[i])
            return parent[i]

        def union(i: int, j: int) -> None:
            root_i, root_j = find(i), find(j)
            if root_i != root_j:
                parent[root_i] = root_j

        # Hard deterministic links (MRN, Phone, Aadhaar)
        by_mrn = defaultdict(list)
        by_phone = defaultdict(list)
        by_aadhaar = defaultdict(list)

        for idx, k in enumerate(keys):
            cand = candidates[k]
            for mrn in cand.mrns:
                if mrn: by_mrn[mrn].append(idx)
            for ph in cand.phones:
                if ph: by_phone[ph].append(idx)
            for aadh in cand.aadhaars:
                if aadh: by_aadhaar[aadh].append(idx)

        for idxs in by_mrn.values():
            for j in range(1, len(idxs)): union(idxs[0], idxs[j])

        for idxs in by_phone.values():
            for j in range(1, len(idxs)): union(idxs[0], idxs[j])

        for idxs in by_aadhaar.values():
            for j in range(1, len(idxs)): union(idxs[0], idxs[j])

        # Aggregate cluster features
        cluster_members = defaultdict(list)
        for idx in range(len(keys)):
            cluster_members[find(idx)].append(idx)

        cluster_names = defaultdict(set)
        cluster_relatives = defaultdict(set)
        cluster_villages = defaultdict(set)

        for root, idxs in cluster_members.items():
            for idx in idxs:
                cand = candidates[keys[idx]]
                cluster_names[root].update(cand.names)
                cluster_relatives[root].update(cand.relatives)
                cluster_villages[root].update(cand.villages)

        # Probabilistic linking with strict secondary confirmation
        roots = sorted(cluster_members.keys())
        for i in range(len(roots)):
            r1 = find(roots[i])
            names1 = cluster_names[r1]
            if not names1:
                continue
            for j in range(i + 1, len(roots)):
                r2 = find(roots[j])
                if r1 == r2:
                    continue
                names2 = cluster_names[r2]
                if not names2:
                    continue

                best_sim = 0
                for n1 in sorted(names1):
                    for n2 in sorted(names2):
                        sim = rapidfuzz.fuzz.token_sort_ratio(n1, n2)
                        if sim > best_sim:
                            best_sim = sim

                if best_sim >= 85:
                    rel_match = bool(cluster_relatives[r1] & cluster_relatives[r2])
                    vil_match = bool(cluster_villages[r1] & cluster_villages[r2])
                    if rel_match or vil_match:
                        union(r1, r2)
                        r_new = find(r1)
                        cluster_names[r_new].update(names1 | names2)
                        cluster_relatives[r_new].update(cluster_relatives[r1] | cluster_relatives[r2])
                        cluster_villages[r_new].update(cluster_villages[r1] | cluster_villages[r2])

        # Collect raw names per cluster and expand with individual first names
        cluster_raw_names = defaultdict(set)
        for idx in range(len(keys)):
            for n in candidates[keys[idx]].raw_names:
                if n:
                    cluster_raw_names[find(idx)].add(n)
                    parts = n.split()
                    if len(parts) >= 2 and len(parts[0]) >= 3 and parts[0].lower() not in ("shri", "smt", "dr", "mr", "mrs", "ms"):
                        cluster_raw_names[find(idx)].add(parts[0])

        # Mint deterministic pseudonyms using RAKSHA_SECRET
        pseudonym_map: dict[tuple[str, str], str | None] = {}
        record_raw_names: dict[tuple[str, str], set[str]] = {}
        cluster_pseudonyms: dict[int, str] = {}

        for idx, k in enumerate(keys):
            root = find(idx)
            cand = candidates[k]
            record_raw_names[k] = cluster_raw_names[root] | cand.raw_names

            # Anti-over-merge: if candidate has no hard ID and no corroborating signals, leave unlinked (null)
            has_hard_id = bool(cand.mrns or cand.phones or cand.aadhaars)
            has_secondary = bool(cluster_relatives[root] or cluster_villages[root])
            if not has_hard_id and not has_secondary:
                pseudonym_map[k] = None
                continue

            if root not in cluster_pseudonyms:
                # Find canonical seed for cluster
                cluster_all_hard = []
                for m_idx in cluster_members[root]:
                    c_cand = candidates[keys[m_idx]]
                    cluster_all_hard.extend(c_cand.mrns)
                    cluster_all_hard.extend(c_cand.phones)
                    cluster_all_hard.extend(c_cand.aadhaars)

                if cluster_all_hard:
                    seed = sorted(cluster_all_hard)[0]
                elif cluster_names[root]:
                    seed = sorted(cluster_names[root])[0]
                else:
                    seed = f"cluster_{root}"

                digest = hmac.new(secret, seed.encode("utf-8"), hashlib.sha256).hexdigest()[:6].upper()
                cluster_pseudonyms[root] = f"PSN-{digest}"

            pseudonym_map[k] = cluster_pseudonyms[root]

        return pseudonym_map, record_raw_names
