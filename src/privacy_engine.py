"""Privacy Engine: Deterministic Per-Patient Date Shifting, Age Banding & Quasi-Identifier Protection."""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import re
from typing import Any

_DATE_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[T\s]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)?$")
_DATE_DASH_RE = re.compile(r"\b(\d{2})-(\d{2})-(\d{4})\b")
_DATE_SLASH_RE = re.compile(r"\b(\d{2})/(\d{2})/(\d{4})\b")
_DATE_DOT_RE = re.compile(r"\b(\d{2})\.(\d{2})\.(\d{4})\b")
_DATE_ISO_MATCH_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")


def compute_date_shift_days(patient_key: str, secret: bytes, max_days: int = 180) -> int:
    """Computes a deterministic integer shift in [-max_days, +max_days] (non-zero)."""
    if not patient_key:
        patient_key = "default_patient"
    d = hmac.new(secret, b"shift|" + patient_key.encode("utf-8"), hashlib.sha256).digest()
    v = int.from_bytes(d[:4], "big") % (2 * max_days) - max_days
    return v if v != 0 else 1


def shift_date_obj(d: dt.date, delta_days: int) -> dt.date:
    """Safely shifts a date object by delta_days."""
    try:
        return d + dt.timedelta(days=delta_days)
    except OverflowError:
        return d


def shift_date_string(val: str, delta_days: int) -> str:
    """Shifts formatted date string while preserving original format and separator."""
    if not val or not isinstance(val, str) or delta_days == 0:
        return val

    # 1. ISO-8601 Timestamp: YYYY-MM-DDTHH:MM:SS...
    if "T" in val or (len(val) >= 10 and val[4] == "-" and val[7] == "-"):
        try:
            # Parse date portion
            date_part = val[:10]
            rest = val[10:]
            y, m, d = int(date_part[:4]), int(date_part[5:7]), int(date_part[8:10])
            orig_d = dt.date(y, m, d)
            shifted = shift_date_obj(orig_d, delta_days)
            return f"{shifted.year:04d}-{shifted.month:02d}-{shifted.day:02d}{rest}"
        except Exception:
            pass

    # 2. DD/MM/YYYY or MM/DD/YYYY
    m_slash = _DATE_SLASH_RE.match(val.strip())
    if m_slash:
        p1, p2, yr = int(m_slash.group(1)), int(m_slash.group(2)), int(m_slash.group(3))
        # If p1 > 12: must be DD/MM/YYYY
        # If p2 > 12: must be MM/DD/YYYY
        if p1 > 12:
            try:
                shifted = shift_date_obj(dt.date(yr, p2, p1), delta_days)
                return f"{shifted.day:02d}/{shifted.month:02d}/{shifted.year:04d}"
            except Exception:
                pass
        elif p2 > 12:
            try:
                shifted = shift_date_obj(dt.date(yr, p1, p2), delta_days)
                return f"{shifted.month:02d}/{shifted.day:02d}/{shifted.year:04d}"
            except Exception:
                pass
        else:
            # Ambiguous: default DD/MM/YYYY
            try:
                shifted = shift_date_obj(dt.date(yr, p2, p1), delta_days)
                return f"{shifted.day:02d}/{shifted.month:02d}/{shifted.year:04d}"
            except Exception:
                pass

    # 3. DD-MM-YYYY
    m_dash = _DATE_DASH_RE.match(val.strip())
    if m_dash:
        p1, p2, yr = int(m_dash.group(1)), int(m_dash.group(2)), int(m_dash.group(3))
        try:
            shifted = shift_date_obj(dt.date(yr, p2, p1), delta_days)
            return f"{shifted.day:02d}-{shifted.month:02d}-{shifted.year:04d}"
        except Exception:
            pass

    # 4. DD.MM.YYYY
    m_dot = _DATE_DOT_RE.match(val.strip())
    if m_dot:
        p1, p2, yr = int(m_dot.group(1)), int(m_dot.group(2)), int(m_dot.group(3))
        try:
            shifted = shift_date_obj(dt.date(yr, p2, p1), delta_days)
            return f"{shifted.day:02d}.{shifted.month:02d}.{shifted.year:04d}"
        except Exception:
            pass

    return val


def shift_dates_in_text(text: str, delta_days: int) -> str:
    """Finds and shifts all dates embedded in unstructured text."""
    if not text or delta_days == 0:
        return text

    def _replace_slash(m: re.Match) -> str:
        p1, p2, yr = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if p1 > 12:
            try:
                shifted = shift_date_obj(dt.date(yr, p2, p1), delta_days)
                return f"{shifted.day:02d}/{shifted.month:02d}/{shifted.year:04d}"
            except Exception:
                return m.group(0)
        elif p2 > 12:
            try:
                shifted = shift_date_obj(dt.date(yr, p1, p2), delta_days)
                return f"{shifted.month:02d}/{shifted.day:02d}/{shifted.year:04d}"
            except Exception:
                return m.group(0)
        else:
            try:
                shifted = shift_date_obj(dt.date(yr, p2, p1), delta_days)
                return f"{shifted.day:02d}/{shifted.month:02d}/{shifted.year:04d}"
            except Exception:
                return m.group(0)

    def _replace_dash(m: re.Match) -> str:
        p1, p2, yr = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            shifted = shift_date_obj(dt.date(yr, p2, p1), delta_days)
            return f"{shifted.day:02d}-{shifted.month:02d}-{shifted.year:04d}"
        except Exception:
            return m.group(0)

    def _replace_iso(m: re.Match) -> str:
        yr, mo, dy = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            shifted = shift_date_obj(dt.date(yr, mo, dy), delta_days)
            return f"{shifted.year:04d}-{shifted.month:02d}-{shifted.day:02d}"
        except Exception:
            return m.group(0)

    text = _DATE_SLASH_RE.sub(_replace_slash, text)
    text = _DATE_DASH_RE.sub(_replace_dash, text)
    text = _DATE_ISO_MATCH_RE.sub(_replace_iso, text)
    return text


def generalize_age(age_val: Any, band_width: int = 5) -> str:
    """Generalizes numeric age to standard 5-year band ('50-54' or '90+')."""
    if age_val is None or age_val == "":
        return ""
    try:
        # Extract digits
        num_str = re.sub(r"[^\d]", "", str(age_val))
        if not num_str:
            return str(age_val)
        age = int(num_str)
        if age >= 90:
            return "90+"
        lo = (age // band_width) * band_width
        return f"{lo}-{lo + band_width - 1}"
    except Exception:
        return str(age_val)
