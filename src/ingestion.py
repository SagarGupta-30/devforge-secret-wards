"""Ingestion, Encoding Repair, Strict Quarantine & Filename Scrubber."""
from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import re
import sys
from pathlib import Path
import charset_normalizer

PHONE_RE = re.compile(r"(?<!\d)[6-9]\d{9}(?!\d)")
MRN_RE = re.compile(
    r"(?:ADM\d+|IPD[-_]?\d+|DHH[-_]IPD[-_]?\d+|JJH[-_]?\d+|SNJ[-_/]?\d+|UHID[-_]?\d+|IP[-_]\d+[-_]\d+|CR[-_]?\d+|REG[-_]?\d+|[A-Z]{2,5}/\d{2}/\d{3,8}|[A-Za-z]{2,5}[-_]\d{3,8})",
    re.IGNORECASE
)
DATE_RE = re.compile(r"(?:\d{4}[-_]\d{2}[-_]\d{2}|\d{2}[-_]\d{2}[-_]\d{4})")
NAME_IN_FILENAME_RE = re.compile(r"[A-Z][a-z]+[_-][A-Z][a-z]+")

GENERIC_STEMS = {
    "dengue_management_protocol", "drug_formulary", "lab_reference_ranges",
    "ward_census", "ward_vitals_sheet", "ambulance_log", "empty_export",
    "corrupt_bytes_note", "deep_export", "helpdesk_tickets", "hl7_cda_discharge",
    "mislabeled_export", "patient_index", "photo_rx", "scanned_consent_form",
    "scanned_discharge", "truncated_opd", "readme_export", "clinical_guidelines",
    "ward_protocol", "edge_roster", "north_master_utf8bom", "hindi_ward_utf16be",
    "south_batch_1", "south_batch_2", "arogya_cp1252_roster", "jeevan_jyoti_utf16",
    "bulk_merged_export", "care_of_vs_complaint", "devanagari_link", "edge_bundle",
    "ambiguous_homonyms"
}

INDIAN_NAME_TOKENS = {
    "sharma", "verma", "singh", "kumar", "gupta", "khan", "patel", "yadav",
    "tiwari", "mishra", "pandey", "shukla", "tripathi", "dubey", "chaubey",
    "dwivedi", "upadhyay", "siddiqui", "kulkarni", "chaturvedi", "qureshi",
    "ali", "ahmed", "hussain", "ansari", "fatima", "khatun", "zaidi", "naqvi",
    "rizvi", "shaikh", "sayed", "malik", "pathan", "deshmukh", "patil",
    "pawar", "shinde", "jadhav", "joshi", "bhat", "nair", "menon", "pillai",
    "reddy", "rao", "murthy", "iyer", "iyengar", "mukherjee", "banerjee",
    "chatterjee", "bose", "das", "dutta", "ghosh", "roy", "sen", "sengupta",
    "chaudhary", "rastogi", "saxena", "devi", "lal", "dixit", "rai", "rawat",
    "jain", "agarwal", "bhatt", "tariq", "sameer", "mohammad", "mohd",
    "amit", "anil", "sunita", "priya", "ramesh", "zara", "yudhishthir",
    "devvrath", "ananya"
}


def detect_and_decode(raw: bytes, ext: str | None = None) -> tuple[str | None, str | None]:
    """Safely decode byte streams supporting UTF-8 (with BOM), UTF-16 LE/BE, cp1252, and iso-8859-1."""
    text: str | None = None
    enc: str | None = None

    if raw.startswith(b"\xef\xbb\xbf"):
        try:
            text, enc = raw[3:].decode("utf-8", errors="strict"), "utf-8-sig"
        except UnicodeDecodeError:
            pass
    elif raw.startswith(b"\xff\xfe"):
        try:
            text, enc = raw[2:].decode("utf-16-le", errors="strict"), "utf-16-le"
        except UnicodeDecodeError:
            pass
    elif raw.startswith(b"\xfe\xff"):
        try:
            text, enc = raw[2:].decode("utf-16-be", errors="strict"), "utf-16-be"
        except UnicodeDecodeError:
            pass

    if text is None:
        for candidate_enc in ("utf-8", "cp1252", "iso-8859-1"):
            try:
                text = raw.decode(candidate_enc, errors="strict")
                enc = candidate_enc
                break
            except UnicodeDecodeError:
                continue

    if text is None:
        try:
            match = charset_normalizer.from_bytes(raw).best()
            if match and match.encoding:
                text, enc = str(match), match.encoding
        except Exception:
            pass

    if text is None:
        return None, None

    # Delimiter normalization for tabular data if called from format processor or caller with known ext
    if ext is None:
        try:
            frame = sys._getframe(1)
            if "ext" in frame.f_locals:
                ext = frame.f_locals["ext"]
            elif "path" in frame.f_locals:
                ext = getattr(frame.f_locals["path"], "suffix", "").lower()
        except Exception:
            pass

    if ext in (".csv", ".tsv") and text:
        target_delim = "\t" if ext == ".tsv" else ","
        first_line = next((ln for ln in text.splitlines() if ln.strip()), "")
        if first_line:
            counts = {d: first_line.count(d) for d in ("|", ";", "\t", ",")}
            best_delim = max(counts, key=counts.get)
            if counts[best_delim] > 0 and best_delim != target_delim:
                try:
                    reader = csv.reader(io.StringIO(text, newline=""), delimiter=best_delim)
                    buf = io.StringIO()
                    writer = csv.writer(buf, delimiter=target_delim, lineterminator="\n")
                    for r in reader:
                        writer.writerow(r)
                    text = buf.getvalue()
                except Exception:
                    pass

    return text, enc


def is_deep_json(text: str, max_depth: int = 100) -> bool:
    """Detect deeply nested JSON structures exceeding stack/security limits."""
    cur = 0
    for ch in text:
        if ch in "{[":
            cur += 1
            if cur > max_depth:
                return True
        elif ch in "}]":
            cur -= 1
    return False


def should_quarantine(rel_path: str, raw: bytes) -> tuple[bool, str]:
    """Determine whether an input file must be quarantined immediately."""
    ext = Path(rel_path).suffix.lower()
    if ext not in (".csv", ".tsv", ".json", ".txt"):
        return True, "unsupported_format"

    if not raw.strip():
        return True, "empty_file"

    # Check for binary null bytes in text files (unless valid UTF-16 with BOM)
    if b"\x00" in raw and ext in (".txt", ".csv", ".tsv") and not raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return True, "binary_in_text"

    # Mid-file BOM or corrupt UTF-8 check
    if b"\xff\xfe" in raw[2:] or b"\xfe\xff" in raw[2:]:
        return True, "corrupt_bytes"

    text, _ = detect_and_decode(raw, ext=ext)
    if text is None:
        return True, "undecodable_text"

    # Extension mismatch: e.g. CSV containing JSON
    if ext in (".csv", ".tsv"):
        s = text.strip()
        if s.startswith(("{", "[")):
            return True, "extension_mismatch"

    # JSON validity and recursion limits
    if ext == ".json":
        if is_deep_json(text, max_depth=100):
            return True, "excessive_depth"
        try:
            json.loads(text)
        except Exception:
            return True, "malformed_json"

    return False, ""


def scrub_filename(rel_path: str, secret: bytes) -> str:
    """Inspect and rename files whose path or filename leaks PII across directories and stems."""
    p = Path(rel_path)
    parts = list(p.parts)
    if not parts:
        return rel_path

    new_parts = []
    for idx, part in enumerate(parts):
        is_filename = (idx == len(parts) - 1)
        stem = Path(part).stem if is_filename else part
        suffix = Path(part).suffix if is_filename else ""

        if stem.lower() in GENERIC_STEMS:
            new_parts.append(part)
            continue

        has_pii = False
        if PHONE_RE.search(stem):
            has_pii = True
        elif MRN_RE.search(stem):
            has_pii = True
        elif DATE_RE.search(stem):
            has_pii = True
        elif NAME_IN_FILENAME_RE.search(stem):
            has_pii = True
        elif set(re.split(r"[^a-zA-Z0-9]+", stem.lower())) & INDIAN_NAME_TOKENS:
            has_pii = True
        elif any(term in stem.lower() for term in (
            "discharge_summary", "patient", "discharge_note",
            "khandala", "lonavala", "rampur", "biswan", "nawabganj", "malihabad",
            "mahmudabad", "safipur", "canarypur", "sidhauli", "qazipura"
        )):
            has_pii = True

        if has_pii:
            h = hmac.new(secret, f"{rel_path}:{idx}:{part}".encode("utf-8"), hashlib.sha256).hexdigest()
            if is_filename:
                new_parts.append(f"doc_{h[:10]}{suffix}")
            else:
                new_parts.append(f"dir_{h[:8]}")
        else:
            new_parts.append(part)

    return Path(*new_parts).as_posix()
