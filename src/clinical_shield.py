"""Clinical Shield (Pass A): Protects clinicians, dosages, vitals, eponyms, and symptoms."""
from __future__ import annotations

import re

# Clinician lines: Dr. <Name> or Dr <Name> with optional credentials
DR_RE = re.compile(
    r"\bDr\.?\s+[A-Za-z]+(?:\s+[A-Za-z]+){0,3}(?:\s*\([A-Za-z0-9\s,.-]+\))?",
    re.UNICODE
)

# Complaints / symptoms: C/O <lowercase symptoms/durations> or Hindi terms
CO_SYMPTOM_RE = re.compile(
    r"\b[Cc]/[Oo]\s*[:\-]?\s*(?:[a-z\u0900-\u097F]+(?:\s+(?!x\b)[a-z\u0900-\u097F]+){0,5}(?:\s*x\s*\d+\s*(?:days?|d|wks?|weeks?|months?|yrs?|years?))?)",
    re.UNICODE
)

# Known Case of / History of
KCO_RE = re.compile(
    r"\b(?:K/C/O|k/c/o|H/O|h/o)\s*[:\-]?\s*[A-Za-z0-9\u0900-\u097F'\s,-]+?(?=(?:\.|\n|;|\s*Attendant|\s*Mob|\s*Adv|\s*Rx|$))",
    re.UNICODE
)

# Medical eponyms
EPONYM_RE = re.compile(
    r"\b(?:Addison|Parkinson|Alzheimer|Hodgkin|Cushing|Bell|Graves|Crohn|Hashimoto|Kawasaki|Meniere|Wilson|Raynaud|Barrett|Huntington)(?:'s|s)?(?:\s+(?:disease|syndrome|palsy|chorea|thyroiditis))?\b",
    re.IGNORECASE | re.UNICODE
)

# Common Indian & global drug names, strengths, formulations
DRUG_RE = re.compile(
    r"\b(?:Tab\.?|Cap\.?|Syp\.?|Inj\.?|Oint\.?)?\s*(?:Dolo|Crocin|Paracetamol|Augmentin|Azithral|Pan-D|Pantop|Pantoprazole|Ramipril|Telma|Telmisartan|Metformin|Atorva|Atorvastatin|Ecosprin|Combiflam|Calpol|Amlodipine|Levothyroxine|Metoprolol|Losartan|Cetirizine|Amoxicillin|Azithromycin|Ciprofloxacin|Ofloxacin|Insulin|Metronidazole|Ceftriaxone|Ondansetron|Ranitidine|Rabeprazole|Diclofenac|Ibuprofen|Tramadol|Montelukast|PCM|ORS|Zinc|Iron|Folic Acid)\s*(?:\d+(?:\.\d+)?\s*(?:mg|g|mcg|ml|IU))?\b",
    re.IGNORECASE | re.UNICODE
)

# Morphological drug suffixes across all medical specialties
DRUG_SUFFIX_RE = re.compile(
    r"\b(?:Tab\.?|Cap\.?|Syp\.?|Inj\.?|Oint\.?|Drops?|Sachet)?\s*"
    r"[A-Za-z]+(?:mab|nib|pril|sartan|olol|statin|mycin|cillin|floxacin|prazole|formin|gliptin|tidine|zepam|fenac|mol)\b"
    r"(?:\s+\d+(?:\.\d+)?\s*(?:mg|g|mcg|ml|IU))?",
    re.IGNORECASE | re.UNICODE
)
FORMULATION_RE = re.compile(
    r"\b(?:Tab\.?|Cap\.?|Syp\.?|Inj\.?|Oint\.?|Drops?|Sachet)\s+[A-Za-z0-9+-]+"
    r"(?:\s+\d+(?:\.\d+)?\s*(?:mg|g|mcg|ml|IU))?\b",
    re.IGNORECASE | re.UNICODE
)

# Dosages & frequencies
DOSES_RE = re.compile(
    r"\b(?:1-0-1|1-1-1|0-0-1|1-0-0|0-1-0|0-0-0|1-1-0|0-1-1|BD|TDS|OD|QID|SOS|HS|PRN|STAT)\s*(?:x\s*\d+\s*(?:days|d|wks|weeks|months))?\b",
    re.UNICODE
)
DURATION_HINDI_RE = re.compile(r"\b\d+\s*दिन\s*(?:से|तक)?\b", re.UNICODE)

# Vitals & lab measurements
VITALS_RE = re.compile(
    r"\b(?:BP\s*[:\-]?\s*\d{2,3}/\d{2,3}|pulse\s*[:\-]?\s*\d{2,3}|SpO2\s*[:\-]?\s*\d{2,3}%?|HR\s*[:\-]?\s*\d{2,3}|Temp(?:erature)?\s*[:\-]?\s*\d{2,3}(?:\.\d+)?(?:\s*F|\s*C)?|RBS\s*[:\-]?\s*\d{2,3}(?:\s*mg/dL)?|FBS\s*[:\-]?\s*\d{2,3}(?:\s*mg/dL)?|HbA1c\s*[:\-]?\s*\d+(?:\.\d+)?%?)\b",
    re.IGNORECASE | re.UNICODE
)
UNITS_RE = re.compile(r"\b\d+(?:\.\d+)?\s*(?:mg/dL|g/dL|mmol/L|mEq/L|uL|mcL|/cumm|IU/L)\b", re.UNICODE)
WARDS_RE = re.compile(r"\b(?:Ward\s*\d+\s*\([A-Za-z]+\)|(?:Bed\s*\d+|ICU|CCU|NICU|PICU|OPD|IPD)(?![-_/0-9])\b)", re.UNICODE)
ADV_RE = re.compile(r"\bAdv\s*[:\-]\s*[^\n]+", re.UNICODE)


class ClinicalShield:
    """Masks clinical terms before PII redaction and restores them intact afterwards."""

    SHIELD_PATTERNS = [
        DR_RE,
        CO_SYMPTOM_RE,
        KCO_RE,
        EPONYM_RE,
        DRUG_RE,
        DRUG_SUFFIX_RE,
        FORMULATION_RE,
        DOSES_RE,
        DURATION_HINDI_RE,
        VITALS_RE,
        UNITS_RE,
        WARDS_RE,
        ADV_RE,
    ]

    @classmethod
    def protect(cls, text: str) -> tuple[str, dict[str, str]]:
        mapping: dict[str, str] = {}

        def repl(match: re.Match) -> str:
            val = match.group(0)
            key = f"__CLIN_SHIELD_{len(mapping):04d}__"
            mapping[key] = val
            return key

        masked = text
        for pat in cls.SHIELD_PATTERNS:
            masked = pat.sub(repl, masked)

        return masked, mapping

    @classmethod
    def restore(cls, text: str, mapping: dict[str, str]) -> str:
        for key, orig in mapping.items():
            text = text.replace(key, orig)
        return text
