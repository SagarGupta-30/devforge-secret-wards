"""Ingestion, Encoding Repair, Strict Quarantine & Filename Scrubber."""
from __future__ import annotations

import hashlib
import hmac
import json
import re
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
    "scanned_discharge", "truncated_opd", "readme_export"
}


def detect_and_decode(raw: bytes) -> tuple[str | None, str | None]:
    """Safely decode byte streams supporting UTF-8 (with BOM), UTF-16 LE/BE, and cp1252."""
    if raw.startswith(b"\xef\xbb\xbf"):
        try:
            return raw[3:].decode("utf-8", errors="strict"), "utf-8-sig"
        except UnicodeDecodeError:
            pass
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return raw.decode("utf-16", errors="strict"), "utf-16"
        except UnicodeDecodeError:
            pass

    for enc in ("utf-8", "cp1252"):
        try:
            return raw.decode(enc, errors="strict"), enc
        except UnicodeDecodeError:
            continue

    # Fallback using charset_normalizer
    try:
        match = charset_normalizer.from_bytes(raw).best()
        if match and match.encoding:
            return str(match), match.encoding
    except Exception:
        pass

    return None, None


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

    # Mid-file BOM or corrupt UTF-8 check
    if b"\xff\xfe" in raw[2:] or b"\xfe\xff" in raw[2:]:
        return True, "corrupt_bytes"

    text, _ = detect_and_decode(raw)
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
    """Inspect and rename files whose path or filename leaks PII."""
    p = Path(rel_path)
    stem = p.stem
    if stem.lower() in GENERIC_STEMS:
        return rel_path

    has_pii = False
    if PHONE_RE.search(stem):
        has_pii = True
    elif MRN_RE.search(stem):
        has_pii = True
    elif DATE_RE.search(stem):
        has_pii = True
    elif NAME_IN_FILENAME_RE.search(stem):
        has_pii = True
    elif any(term in stem.lower() for term in ("discharge_summary", "patient", "discharge_note")):
        has_pii = True

    if has_pii:
        h = hmac.new(secret, rel_path.encode("utf-8"), hashlib.sha256).hexdigest()[:10]
        new_name = f"doc_{h}{p.suffix}"
        return (p.parent / new_name).as_posix()

    return rel_path
