#!/usr/bin/env python3
"""Secret Wards Privacy Firewall (Sprint 3): 100% Offline, CPU-only, Multilingual & Re-ID Defense."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import sys
import time
from pathlib import Path

from src.ingestion import should_quarantine, scrub_filename, detect_and_decode
from src.pii_extractor import PIIExtractor
from src.record_linker import RecordLinker
from src.privacy_engine import compute_date_shift_days


# --------------------------------------------------------------------------- policy & secret
def load_policy(path: Path) -> dict:
    """Loads key: value rules from config/policy.yaml."""
    policy = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if ":" in line:
                k, v = (x.strip() for x in line.split(":", 1))
                policy[k] = int(v) if v.lstrip("-").isdigit() else v
    return policy


HERE = Path(__file__).resolve().parent
POLICY = load_policy(HERE / "config" / "policy.yaml")
DATE_SHIFT_MAX_DAYS = int(POLICY.get("date_shift_max_days", 180))
AGE_BAND_YEARS = int(POLICY.get("age_band_years", 5))

raw_secret = os.environ.get("RAKSHA_SECRET", "")
if not raw_secret:
    sys.stderr.write("WARNING: RAKSHA_SECRET environment variable is unset or empty; falling back to ephemeral random key\n")
    SECRET = os.urandom(32).hex().encode("utf-8")
else:
    SECRET = raw_secret.encode("utf-8")


# --------------------------------------------------------------------------- stats
class Stats:
    identifiers = 0
    records = 0
    linked = 0
    generalized = 0
    suppressed = 0


def update_token_stats(cleaned_text: str) -> None:
    """Updates report statistics based on detected, suppressed, and generalized tokens."""
    Stats.identifiers += len(re.findall(r"\[(?:PATIENT_NAME|RELATIVE_NAME|PHONE|AADHAAR|AADHAAR_MASKED|ABHA_NUMBER|ABHA_ADDRESS|PAN|INSURANCE_ID|MRN|EMAIL|PSN-[A-F0-9]+)\]", cleaned_text))
    Stats.suppressed += len(re.findall(r"\[(?:VILLAGE|LOCALITY|PIN|HOUSE|LANDMARK|ADDRESS)\]", cleaned_text))
    Stats.generalized += len(re.findall(r"\b\d{1,2}-\d{1,2}\b|\b90\+\b", cleaned_text))


# --------------------------------------------------------------------------- format processors
def process_csv(
    text: str,
    delimiter: str,
    rel: str,
    out_rel: str,
    pseudonym_map: dict,
    record_names_map: dict,
    records: list,
    secret: bytes
) -> str:
    rows = list(csv.reader(io.StringIO(text, newline=""), delimiter=delimiter))
    if not rows:
        return ""
    header, body = rows[0], rows[1:]
    clean_header = PIIExtractor.clean_csv_header(header)

    buf = io.StringIO()
    w = csv.writer(buf, delimiter=delimiter, lineterminator="\n")
    w.writerow(clean_header)

    for i, row in enumerate(body):
        loc = f"row:{i}"
        pid = pseudonym_map.get((rel, loc))
        known_names = record_names_map.get((rel, loc))
        delta_days = compute_date_shift_days(pid or f"{rel}_{loc}", secret, max_days=DATE_SHIFT_MAX_DAYS)

        cleaned_row = PIIExtractor.clean_csv_row(row, header, pid=pid, delta_days=delta_days, known_names=known_names)
        w.writerow(cleaned_row)
        records.append({"file": f"sanitized/{out_rel}", "loc": loc, "patient": pid})
        Stats.records += 1
        if pid:
            Stats.linked += 1

    res = buf.getvalue()
    update_token_stats(res)
    return res


def process_json(
    text: str,
    rel: str,
    out_rel: str,
    pseudonym_map: dict,
    record_names_map: dict,
    records: list,
    secret: bytes
) -> str:
    data = json.loads(text)

    # 1. Patients array (e.g. registration exports)
    if isinstance(data, dict) and "patients" in data and isinstance(data["patients"], list):
        cleaned_patients = []
        for i, pat in enumerate(data.get("patients", [])):
            loc = f"/patients/{i}"
            pid = pseudonym_map.get((rel, loc))
            records.append({"file": f"sanitized/{out_rel}", "loc": loc, "patient": pid})
            Stats.records += 1
            if pid: Stats.linked += 1
            delta = compute_date_shift_days(pid or f"{rel}_{loc}", secret, max_days=DATE_SHIFT_MAX_DAYS)
            cleaned_pat = PIIExtractor.clean_json_value(
                pat, parent_key="patients", secret=secret, pid=pid, delta_days=delta, known_names=record_names_map.get((rel, loc))
            )
            cleaned_patients.append(cleaned_pat)
        cleaned_data = PIIExtractor.clean_json_value(data, secret=secret)
        cleaned_data["patients"] = cleaned_patients
        res = json.dumps(cleaned_data, ensure_ascii=False, indent=2) + "\n"
        update_token_stats(res)
        return res

    # 2. Visits array (e.g. OPD registers)
    elif isinstance(data, dict) and "visits" in data and isinstance(data["visits"], list):
        cleaned_visits = []
        for i, v in enumerate(data.get("visits", [])):
            loc = f"/visits/{i}"
            pid = pseudonym_map.get((rel, loc))
            records.append({"file": f"sanitized/{out_rel}", "loc": loc, "patient": pid})
            Stats.records += 1
            if pid: Stats.linked += 1
            delta = compute_date_shift_days(pid or f"{rel}_{loc}", secret, max_days=DATE_SHIFT_MAX_DAYS)
            cleaned_v = PIIExtractor.clean_json_value(
                v, parent_key="visits", secret=secret, pid=pid, delta_days=delta, known_names=record_names_map.get((rel, loc))
            )
            cleaned_visits.append(cleaned_v)
        cleaned_data = PIIExtractor.clean_json_value(data, secret=secret)
        cleaned_data["visits"] = cleaned_visits
        res = json.dumps(cleaned_data, ensure_ascii=False, indent=2) + "\n"
        update_token_stats(res)
        return res

    # 3. Lab bulk results array
    elif isinstance(data, dict) and "results" in data and isinstance(data["results"], list) and any("uhid" in r or "patient_name" in r or "patient" in r for r in data["results"][:3]):
        cleaned_results = []
        for i, r in enumerate(data.get("results", [])):
            loc = f"/results/{i}"
            pid = pseudonym_map.get((rel, loc))
            records.append({"file": f"sanitized/{out_rel}", "loc": loc, "patient": pid})
            Stats.records += 1
            if pid: Stats.linked += 1
            delta = compute_date_shift_days(pid or f"{rel}_{loc}", secret, max_days=DATE_SHIFT_MAX_DAYS)
            cleaned_r = PIIExtractor.clean_json_value(
                r, parent_key="results", secret=secret, pid=pid, delta_days=delta, known_names=record_names_map.get((rel, loc))
            )
            cleaned_results.append(cleaned_r)
        cleaned_data = PIIExtractor.clean_json_value(data, secret=secret)
        cleaned_data["results"] = cleaned_results
        res = json.dumps(cleaned_data, ensure_ascii=False, indent=2) + "\n"
        update_token_stats(res)
        return res

    # 4. Standard document JSON (FHIR bundles, single lab reports, opd summaries)
    else:
        loc = "doc"
        pid = pseudonym_map.get((rel, loc))
        records.append({"file": f"sanitized/{out_rel}", "loc": loc, "patient": pid})
        Stats.records += 1
        if pid: Stats.linked += 1
        delta = compute_date_shift_days(pid or f"{rel}_{loc}", secret, max_days=DATE_SHIFT_MAX_DAYS)
        cleaned_data = PIIExtractor.clean_json_value(
            data, secret=secret, pid=pid, delta_days=delta, known_names=record_names_map.get((rel, loc))
        )
        res = json.dumps(cleaned_data, ensure_ascii=False, indent=2) + "\n"
        update_token_stats(res)
        return res


def process_text(
    text: str,
    rel: str,
    out_rel: str,
    pseudonym_map: dict,
    record_names_map: dict,
    records: list,
    secret: bytes
) -> str:
    loc = "doc"
    pid = pseudonym_map.get((rel, loc))
    records.append({"file": f"sanitized/{out_rel}", "loc": loc, "patient": pid})
    Stats.records += 1
    if pid:
        Stats.linked += 1
    delta = compute_date_shift_days(pid or f"{rel}_{loc}", secret, max_days=DATE_SHIFT_MAX_DAYS)
    known_names = record_names_map.get((rel, loc))
    res = PIIExtractor.clean_free_text(text, pid=pid, delta_days=delta, known_names=known_names)
    update_token_stats(res)
    return res


def peak_rss_mb() -> float:
    try:
        import resource
        r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return round(r / 1024 if sys.platform != "darwin" else r / 1048576, 1)
    except Exception:
        return 0.0


# --------------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser(description="Secret Wards Privacy Firewall")
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    a = ap.parse_args()
    t0 = time.monotonic()
    inp, out = Path(a.input), Path(a.output)
    if not inp.exists() or not inp.is_dir():
        sys.stderr.write(f"Error: input directory does not exist or is not a directory: {inp}\n")
        return 1

    san = out / "sanitized"
    san.mkdir(parents=True, exist_ok=True)
    files, records = [], []
    sys.setrecursionlimit(5000)

    # Pass 1: Cross-Script Candidate Extraction & Deterministic Identity Resolution
    candidates = RecordLinker.extract_candidates(inp)
    pseudonym_map, record_names_map = RecordLinker.link_candidates(candidates, SECRET)

    # Pass 2: Sanitization, Redaction & Quasi-Identifier Protection
    for path in sorted(p for p in inp.rglob("*") if p.is_file()):
        rel = path.relative_to(inp).as_posix()
        raw = path.read_bytes()
        entry = {"input_sha256": hashlib.sha256(raw).hexdigest()}
        ext = path.suffix.lower()

        # Step 1: Strict Quarantine Check
        is_bad, reason = should_quarantine(rel, raw)
        if is_bad:
            entry.update(output=None, status="quarantined", reason=reason)
            files.append(entry)
            continue

        # Step 2: Filename Channel Scrubber
        out_rel = scrub_filename(rel, SECRET)

        # Step 3: Decode safely
        text, _ = detect_and_decode(raw)
        if text is None:
            entry.update(output=None, status="quarantined", reason="undecodable_text")
            files.append(entry)
            continue

        try:
            if ext in (".csv", ".tsv"):
                result = process_csv(
                    text, "\t" if ext == ".tsv" else ",", rel, out_rel, pseudonym_map, record_names_map, records, SECRET
                )
            elif ext == ".json":
                result = process_json(
                    text, rel, out_rel, pseudonym_map, record_names_map, records, SECRET
                )
            else:
                result = process_text(
                    text, rel, out_rel, pseudonym_map, record_names_map, records, SECRET
                )

            dest = san / out_rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(result, encoding="utf-8", newline="")
            entry.update(output=f"sanitized/{out_rel}", status="sanitized")
        except Exception as exc:
            reason = str(exc) if isinstance(exc, ValueError) and str(exc).isidentifier() else type(exc).__name__
            entry.update(output=None, status="quarantined", reason=reason)

        files.append(entry)

    # Filter records for sanitized files only
    exported = {e["output"] for e in files if e["output"]}
    records = [r for r in records if r["file"] in exported]

    with open(out / "records.jsonl", "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")

    n_san = sum(e["status"] == "sanitized" for e in files)
    report = {
        "schema": "raksha-report/1",
        "files": files,
        "counts": {
            "files_total": len(files),
            "files_sanitized": n_san,
            "files_quarantined": len(files) - n_san,
            "records_processed": len(records),
            "identifiers_detected": Stats.identifiers,
            "records_linked": sum(r["patient"] is not None for r in records),
            "records_generalized": Stats.generalized,
            "records_suppressed": Stats.suppressed,
            "records_quarantined": 0,
        },
        "timing": {"seconds": round(time.monotonic() - t0, 2)},
        "resources": {"peak_rss_mb": peak_rss_mb()},
    }
    (out / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    # Output strictly summary counts to stdout (Zero Leaks)
    print(f"files {len(files)} sanitized {n_san} quarantined {len(files) - n_san} records {len(records)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
