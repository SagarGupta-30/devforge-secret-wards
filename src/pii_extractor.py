"""PII Extraction & Sanitization Engine (Pass B): All channels, structured & multilingual."""
from __future__ import annotations

import base64
import binascii
import csv
import datetime as dt
import hashlib
import hmac
import io
import re
from typing import Any
from src.clinical_shield import ClinicalShield
from src.privacy_engine import shift_date_string, shift_dates_in_text, generalize_age

# --------------------------------------------------------------------------- Identifiers Regexes
PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}(?!\d)|"
    r"(?<!\d)0[6-9]\d{4}[\s-]?\d{5}(?!\d)|"
    r"(?<!\d)[6-9]\d{9}(?!\d)"
)
PHONE_DEVA_RE = re.compile(r"[६-९][०-९]{9}")
PHONE_LABEL_RE = re.compile(
    r"\b(?:mob|mobile|phone|ph|contact|callback)\s*[:\-]?\s*(?:\+?91[\s-]?)?[6-9]\d{2,4}[\s-]?\d{4,5}\b",
    re.IGNORECASE
)

AADHAAR_RE = re.compile(r"(?<!\d)\d{4}[\s-]?\d{4}[\s-]?\d{4}(?!\d)")
AADHAAR_MASKED_RE = re.compile(
    r"(?<!\d)(?:[xX]{4}[\s-]?){2}\d{4}(?!\d)|"
    r"(?<!\d)[xX]{4}[\s-]?[xX]{4}[\s-]?\d{4}(?!\d)|"
    r"(?<!\d)\d{4}[\s-]?[xX]{4}[\s-]?\d{4}(?!\d)",
    re.IGNORECASE
)

ABHA_NUMBER_RE = re.compile(r"(?<!\d)\d{2}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}(?!\d)")
ABHA_ADDR_RE = re.compile(r"\b[a-zA-Z0-9._-]+@(sbx|abdm)\b")
PAN_RE = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")

MRN_RE = re.compile(
    r"\b(?:SNJ|JJH|ADM|IPD|DHH|IP|UHID|OPD|CR|REG)[-_/]?\d{2,6}[-_/]?\d{2,8}\b|"
    r"\b[A-Z]{2,5}/\d{2}/\d{3,8}\b|"
    r"\b(?:UHID|MRN|IPD|REG\s*NO|CR\s*NO|OPD\s*NO|IP\s*NO|PATIENT\s*ID)[-:\s]*\d+[A-Z0-9-]*\b",
    re.IGNORECASE
)

INSURANCE_RE = re.compile(r"\b(?:PMJAY|INS|POL|TPA)[-_/A-Z0-9]+\b", re.IGNORECASE)
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")

# Targeted PIN patterns (UP PINs 20xxxx-28xxxx or labeled PINs)
PIN_LABEL_RE = re.compile(r"\b(?:PIN|Pin|Pincode|पिन)\s*[:\-]?\s*\d{6}\b", re.IGNORECASE | re.UNICODE)
PIN_UP_RE = re.compile(r"(?:-\s*|\b)(?:2[0-8]\d{4})\b")

# Devanagari Specific Patterns
DEVA_PATIENT_NAME_LABEL = re.compile(r"(?:मरीज़\s*(?:का\s*)?नाम|नाम)\s*[:\-]\s*(.+?)(?=\s+आयु|\s+उम्र|\s*[/,\n]|\s*$|\s{2,})", re.UNICODE)
DEVA_RELATIVE_LABEL = re.compile(r"(?:पिता/पति|पिता|पति)\s*(?:का\s*नाम)?\s*[:\-]\s*([^\n,;|/]+)", re.UNICODE)
DEVA_MRN_LABEL = re.compile(r"(?:पंजीकरण|आईपीडी)\s*(?:सं\.?|संख्या)\s*[:\-]\s*([^\n,;|/]+)", re.UNICODE)
DEVA_PHONE_LABEL = re.compile(r"मोबाइल\s*[:\-]\s*([^\n,;|/]+)", re.UNICODE)
DEVA_DOB_LABEL = re.compile(r"जन्म\s*तिथि\s*[:\-]\s*([^\n,;|/]+)", re.UNICODE)

DEVA_ADDRESS_LINE = re.compile(r"पता\s*[:\-]\s*([^\n]+)", re.UNICODE)
DEVA_VILLAGE_LABEL = re.compile(r"ग्राम\s*[:\-]?\s*[\u0900-\u097F\s]+?(?=(?:,|\.|\n|;|तहसील|जिला|पिन|$))", re.UNICODE)
DEVA_TEHSIL_LABEL = re.compile(r"तहसील\s*[:\-]?\s*[\u0900-\u097F\s]+?(?=(?:,|\.|\n|;|जिला|ग्राम|पिन|$))", re.UNICODE)
DEVA_MOHALLA_LABEL = re.compile(r"मोहल्ला\s*[:\-]?\s*[\u0900-\u097F\s]+?(?=(?:,|\.|\n|;|ग्राम|तहसील|$))", re.UNICODE)
DEVA_LANDMARK_LABEL = re.compile(r"[\u0900-\u097F\s]+?\s*के\s*पास", re.UNICODE)
DEVA_HOUSE_LABEL = re.compile(r"मकान\s*(?:नं\.?)?\s*[-:\s]*\w+", re.UNICODE)
DEVA_WARD_LABEL = re.compile(r"वार्ड\s*(?:नं\.?)?\s*\d+\b", re.UNICODE)

DEVA_IN_SENTENCE_NAME = re.compile(r"(?<![A-Za-z\u0900-\u097F])([\u0900-\u097F]+(?:\s+[\u0900-\u097F]+){0,2})\s+को\s+", re.UNICODE)
DEVA_HONORIFIC_NAME = re.compile(r"([^\n,;|/\s]+(?:\s+[^\n,;|/\s]+)?)\s+(?:जी|साहब|साहिब)", re.UNICODE)
DEVA_SURNAME_NAME = re.compile(
    r"\b([^\n,;|/\s]+)\s+(?:देवी|कुमार|शर्मा|वर्मा|सिंह|खान|अंसारी|चौहान|गुप्ता|यादव|पांडे|तिवारी|निषाद|मिश्रा|पटेल)\b",
    re.UNICODE
)

# Hinglish & English Context Patterns
ADDRESS_LINE_RE = re.compile(r"\bAddress(?:\s*\([^)]*\))?\s*:\s*[^\n]+", re.IGNORECASE)
ADDRESS_HOUSE_RE = re.compile(r"\b(?:H\.?No\.?|House\s*No\.?|House|Makan\s*No\.?|Makan)\s*[-:\s]*\w+\b", re.IGNORECASE)
ADDRESS_LANDMARK_RE = re.compile(r"\b(?:near|opp\.?|behind|beside)\s+[A-Za-z0-9\s]+?(?=(?:,|\.|\n|;|-|Vill|Tehsil|Distt?|Ward|Mohalla|$))", re.IGNORECASE)
# Standalone Gazetteer of villages, tehsils, and localities in UP / India
GAZETTEER_VILLAGES = {
    "bilgram", "bhimpur", "sandila", "shahabad", "sawayajpur", "harpalpur",
    "mallawan", "madhoganj", "beniganj", "kachauna", "pali", "pihani",
    "gopamau", "kursath", "kalyanpur", "shivrajpur", "bithoor", "ghatampur",
    "bilhaur", "bansi", "itwa", "dumariyaganj", "shohratgarh", "naugarh",
    "aliganj", "bakshi ka talab", "mohanlalganj", "malihabad", "sarojini nagar",
    "chinhat", "kakori", "goshainganj", "amethi", "rampur", "bhagwantpur",
    "fatehpur", "daulatpur", "mirzapur", "govindpur", "ramnagar", "kishunpur",
    "chhibramau", "tirwa", "talgram", "harchandpur", "lalganj", "salon",
    "tiloi", "dhanepur", "mankapur", "tarabganj", "colonelganj", "paraspur"
}

ADDRESS_VILLAGE_RE = re.compile(
    r"\b(?:Vill(?:age)?\b|Vill\.|Gram\b|Gaon\b|Post(?:\s*Office)?\b|P\.?O\.?|Tehsil\b|Kasba\b|Town\b|City\b)\s*[:\-]?\s*([A-Za-z\s]+?)(?=(?:,|\.|\n|;|-|\bVill\b|\bGram\b|\bPost\b|\bTehsil\b|\bDistt?\b|\bWard\b|\bMohalla\b|\bPincode\b|\bPin\b|\d|$))",
    re.IGNORECASE
)
ADDRESS_TEHSIL_RE = re.compile(r"\bTehsil\s+[A-Za-z\s]+?(?=(?:,|\.|\n|;|-|Distt?|Vill|$))", re.IGNORECASE)
ADDRESS_LOCALITY_RE = re.compile(r"\b(?:Mohalla|Bazar|Nagar|Colony|Ganj|Sector)\s+[A-Za-z0-9\s]+?(?=(?:,|\.|\n|;|-|Vill|Tehsil|$))", re.IGNORECASE)
ADDRESS_WARD_RE = re.compile(r"\bWard\s*\d+\b", re.IGNORECASE)

ATTENDANT_RE = re.compile(r"\bAttendant\s*:\s*([A-Za-z\s]+?)(?=(?:,|\.|\n|;|-|Mob|\d|$))", re.IGNORECASE)
ATTENDANT_SAMPLE_RE = re.compile(r"\b([A-Za-z]+)'s\s+attendant\b", re.IGNORECASE)
CO_CARE_OF_RE = re.compile(r"\bC/O\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})\b")
HINGLISH_KA_RE = re.compile(r"\b([A-Za-z]+(?:\s+(?:amma|ji|babu|sahab|saheb|didi|bhai))?)\s+(?:ka|ki|ke)\s+", re.IGNORECASE)
DISCHARGE_NOTE_NAME_RE = re.compile(r"(?:Discharge\s*Note|Note)\s*[:\-]?\s*([A-Za-z]+)\b", re.IGNORECASE)

NAME_LABEL_RE = re.compile(
    r"\b(?:Patient\s*Name|Patient|Pt\.?|Visitor|Witnessed by|Called by|Referral|Referred to|Ref by)\s*[:\-]?\s*(?!\s*(?:S/o|D/o|W/o|C/o|c/o|Care\s*of|Father|Guardian|Husband|Wife|Son|Daughter|Mother|Age|आयु|DOB|Mob|Phone|Address|Vill|\[RELATIVE_NAME\]))([A-Za-z]+(?:\s+[A-Za-z]+){0,3})",
    re.IGNORECASE
)
RELATIVE_RE = re.compile(
    r"\b(S/o|D/o|W/o|C/o|c/o|Care\s*of|Son\s*of|Daughter\s*of|Wife\s*of|Guardian|Relative|Father(?:'s)?(?:\s*name)?|Husband(?:'s)?(?:\s*name)?|Mother(?:'s)?(?:\s*name)?|Spouse(?:\s*name)?|Pita|Pati|Mata)\b\s*[:\-]?\s*([A-Z][a-z]+(?:[^\S\r\n]+[A-Z][a-z]+){0,2})",
    re.IGNORECASE | re.UNICODE
)
HONORIFIC_RE = re.compile(
    r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+(?:sahab|saheb|ji|didi|bhaiya|bhai|behenji|babu|babuji|amma)\b",
    re.IGNORECASE
)
WHATSAPP_SENDER_RE = re.compile(
    r"\[\d{1,2}/\d{1,2}/\d{2,4},\s*\d{1,2}:\d{2}(?::\d{2})?\s*(?:am|pm)?\]\s*([^:\n]+):",
    re.IGNORECASE
)
INITIALS_NAME_RE = re.compile(
    r"\b(?:Md\.|Mohd\.|Mohammad|Mohammed|Shri|Smt\.|Kumari|Mr\.|Mrs\.|Ms\.)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})\b"
)
LOG_VISITOR_RE = re.compile(r"\b\d{2}:\d{2}\s*-\s*([A-Z][a-z]+\s+[A-Z][a-z]+)\b")

DOB_LABEL_RE = re.compile(
    r"\b(?:DOB|Date\s*of\s*Birth)\s*[:\-]?\s*(\d{1,2}[-/\.]\d{1,2}[-/\.]\d{2,4}|\d{4}[-/\.]\d{1,2}[-/\.]\d{1,2})",
    re.IGNORECASE
)
AGE_LABEL_RE = re.compile(r"\bAge(?:/Sex)?\s*[:\-]?\s*(\d{1,3})\s*(?:Yrs?|years?|yr)?", re.IGNORECASE)
DEVA_AGE_LABEL_RE = re.compile(r"आयु\s*[:\-]?\s*(\d{1,3})\s*वर्ष", re.UNICODE)


# --------------------------------------------------------------------------- Extraction Core
class PIIExtractor:
    identifiers_count = 0

    @classmethod
    def clean_free_text(cls, text: str, pid: str | None = None, delta_days: int = 0, known_names: set[str] | None = None) -> str:
        """Sanitizes unstructured text across English, Hindi, and Hinglish."""
        if not text:
            return text

        p_token = f"[{pid}]" if pid else "[PATIENT_NAME]"

        # 1. Pass A: Protect clinical terms
        shielded, mapping = ClinicalShield.protect(text)

        # 2. Devanagari labels
        shielded = DEVA_PATIENT_NAME_LABEL.sub(f"मरीज़ का नाम: {p_token}", shielded)
        shielded = DEVA_RELATIVE_LABEL.sub("पिता/पति: [RELATIVE_NAME]", shielded)
        shielded = DEVA_MRN_LABEL.sub("पंजीकरण सं.: [MRN]", shielded)
        shielded = DEVA_PHONE_LABEL.sub("मोबाइल: [PHONE]", shielded)
        shielded = DEVA_DOB_LABEL.sub("जन्म तिथि: [DOB]", shielded)

        # Devanagari addresses
        shielded = DEVA_ADDRESS_LINE.sub("पता: [ADDRESS]", shielded)
        shielded = DEVA_VILLAGE_LABEL.sub("[VILLAGE]", shielded)
        shielded = DEVA_TEHSIL_LABEL.sub("[LOCALITY]", shielded)
        shielded = DEVA_MOHALLA_LABEL.sub("[LOCALITY]", shielded)
        shielded = DEVA_LANDMARK_LABEL.sub("[LANDMARK]", shielded)
        shielded = DEVA_HOUSE_LABEL.sub("[HOUSE]", shielded)
        shielded = DEVA_WARD_LABEL.sub("[LOCALITY]", shielded)

        # Devanagari sentence & honorific names
        shielded = DEVA_IN_SENTENCE_NAME.sub(
            lambda m: f"{p_token} को " if "मरीज़" not in m.group(1) else m.group(0),
            shielded
        )
        shielded = DEVA_HONORIFIC_NAME.sub(f"{p_token}", shielded)
        shielded = DEVA_SURNAME_NAME.sub(f"{p_token}", shielded)

        # 3. Hinglish & English Contextual Names & Addresses
        shielded = ADDRESS_LINE_RE.sub("Address: [ADDRESS]", shielded)
        shielded = WHATSAPP_SENDER_RE.sub("[CHAT_MESSAGE_HEADER]:", shielded)
        shielded = LOG_VISITOR_RE.sub("[VISITOR_ENTRY]", shielded)
        shielded = ATTENDANT_SAMPLE_RE.sub(f"{p_token}'s attendant", shielded)
        shielded = ATTENDANT_RE.sub("Attendant: [RELATIVE_NAME]", shielded)

        # Relatives & Guardians FIRST before Name Label
        rel_names_found: set[str] = set()
        def _repl_rel(m: re.Match) -> str:
            prefix = m.group(1)
            sep = ": " if ":" in m.group(0) else " "
            r_name = m.group(2).strip()
            if len(r_name) >= 3:
                rel_names_found.add(r_name)
            return f"{prefix}{sep}[RELATIVE_NAME]"

        shielded = RELATIVE_RE.sub(_repl_rel, shielded)
        for r_name in rel_names_found:
            shielded = re.sub(
                r"(?<![A-Za-z\u0900-\u097F])" + re.escape(r_name) + r"(?![A-Za-z\u0900-\u097F])",
                "[RELATIVE_NAME]",
                shielded
            )

        shielded = CO_CARE_OF_RE.sub("C/O [RELATIVE_NAME]", shielded)
        shielded = HINGLISH_KA_RE.sub(f"{p_token} ka ", shielded)
        shielded = DISCHARGE_NOTE_NAME_RE.sub(f"Discharge Note: {p_token}", shielded)
        shielded = NAME_LABEL_RE.sub(f"Patient Name: {p_token}", shielded)
        shielded = HONORIFIC_RE.sub(p_token, shielded)
        shielded = INITIALS_NAME_RE.sub(p_token, shielded)

        # 4. Standard IDs and Contacts
        shielded = PHONE_LABEL_RE.sub("[PHONE]", shielded)
        shielded = PHONE_DEVA_RE.sub("[PHONE]", shielded)
        shielded = AADHAAR_MASKED_RE.sub("[AADHAAR_MASKED]", shielded)
        shielded = AADHAAR_RE.sub("[AADHAAR]", shielded)
        shielded = ABHA_NUMBER_RE.sub("[ABHA_NUMBER]", shielded)
        shielded = ABHA_ADDR_RE.sub("[ABHA_ADDRESS]", shielded)
        shielded = PHONE_RE.sub("[PHONE]", shielded)
        shielded = EMAIL_RE.sub("[EMAIL]", shielded)
        shielded = PAN_RE.sub("[PAN]", shielded)
        shielded = MRN_RE.sub(f"[{pid}]" if pid else "[MRN]", shielded)
        shielded = INSURANCE_RE.sub("[INSURANCE_ID]", shielded)

        # 5. Address parts & PIN
        shielded = ADDRESS_LANDMARK_RE.sub("[LANDMARK]", shielded)
        shielded = ADDRESS_VILLAGE_RE.sub("[VILLAGE]", shielded)

        # Standalone gazetteer villages
        for v_name in sorted(GAZETTEER_VILLAGES, key=lambda x: -len(x)):
            pat = re.compile(r"(?<![A-Za-z\u0900-\u097F])" + re.escape(v_name) + r"(?![A-Za-z\u0900-\u097F])", re.IGNORECASE)
            shielded = pat.sub("[VILLAGE]", shielded)

        shielded = ADDRESS_TEHSIL_RE.sub("[LOCALITY]", shielded)
        shielded = ADDRESS_LOCALITY_RE.sub("[LOCALITY]", shielded)
        shielded = ADDRESS_WARD_RE.sub("[LOCALITY]", shielded)
        shielded = ADDRESS_HOUSE_RE.sub("[HOUSE]", shielded)
        shielded = PIN_LABEL_RE.sub("[PIN]", shielded)
        shielded = PIN_UP_RE.sub("[PIN]", shielded)

        # 6. Age & DOB generalization
        shielded = DOB_LABEL_RE.sub("DOB: [DOB]", shielded)
        shielded = AGE_LABEL_RE.sub(lambda m: f"Age: {generalize_age(m.group(1))}", shielded)
        shielded = DEVA_AGE_LABEL_RE.sub(lambda m: f"आयु: {generalize_age(m.group(1))} वर्ष", shielded)

        # 7. Redact known patient names in text if available (Unicode-safe word boundaries, longest first)
        if known_names:
            for name in sorted(known_names, key=lambda x: (-len(x), x)):
                if name and len(name) >= 3:
                    pattern = re.compile(r"(?<![A-Za-z\u0900-\u097F])" + re.escape(name) + r"(?![A-Za-z\u0900-\u097F])", re.IGNORECASE | re.UNICODE)
                    shielded = pattern.sub(p_token, shielded)

        # 8. Date shifting
        if delta_days != 0:
            shielded = shift_dates_in_text(shielded, delta_days)

        # 9. Pass C: Restore protected clinical terms
        restored = ClinicalShield.restore(shielded, mapping)
        return restored

    @classmethod
    def clean_base64_string(cls, b64_str: str, pid: str | None = None) -> str:
        """Decodes base64, cleans internal PII, and returns re-encoded base64."""
        tok = b64_str.strip()
        try:
            padded = tok + "=" * (-len(tok) % 4)
            decoded = base64.b64decode(padded).decode("utf-8")
            cleaned = cls.clean_free_text(decoded, pid=pid)
            return base64.b64encode(cleaned.encode("utf-8")).decode("ascii")
        except Exception:
            return b64_str

    @classmethod
    def clean_metadata_path(cls, path_str: str) -> str:
        """Sanitizes filenames or file paths embedded inside metadata."""
        if not path_str or not isinstance(path_str, str):
            return path_str
        path_str = re.sub(r"[A-Z][a-z]+_[A-Z][a-z]+(?:_\d+)?", "[PATIENT_EXPORT_DIR]", path_str)
        path_str = re.sub(r"(?<=[\\/])[A-Za-z]+_[A-Za-z]+[^\\]*(?=[\\/])", "[PATIENT_EXPORT_DIR]", path_str)
        parts = re.split(r"([\\/])", path_str)
        cleaned_parts = []
        for part in parts:
            if part in ("\\", "/"):
                cleaned_parts.append(part)
            else:
                cleaned_parts.append(cls.clean_free_text(part))
        return "".join(cleaned_parts)

    @classmethod
    def clean_json_value(
        cls,
        val: Any,
        parent_key: str = "",
        secret: bytes = b"",
        pid: str | None = None,
        delta_days: int = 0,
        known_names: set[str] | None = None,
    ) -> Any:
        """Recursively sanitizes JSON values, scrubbing metadata, keys, base64, and content."""
        p_token = f"[{pid}]" if pid else "[PATIENT_NAME]"
        id_token = f"[{pid}]" if pid else "[MRN]"

        if isinstance(val, dict):
            new_dict = {}
            res_type = val.get("resourceType")
            is_clinical_org_or_practitioner = res_type in ("Organization", "Practitioner")
            is_patient_resource = res_type == "Patient"

            # Check FHIR Binary
            if res_type == "Binary" and "data" in val and isinstance(val["data"], str):
                val = dict(val)
                val["data"] = cls.clean_base64_string(val["data"], pid=pid)

            # Check attachment dict
            if "attachment" in val and isinstance(val["attachment"], dict) and "data" in val["attachment"]:
                val = dict(val)
                val["attachment"] = dict(val["attachment"])
                val["attachment"]["data"] = cls.clean_base64_string(val["attachment"]["data"], pid=pid)

            # Check FHIR extension with mothers-name or relative name
            if "url" in val:
                url_str = str(val.get("url", "")).lower()
                if any(x in url_str for x in ("mothers-name", "mother", "father", "guardian", "relative")):
                    val = dict(val)
                    val["valueString"] = "[RELATIVE_NAME]"
                elif any(x in url_str for x in ("patient-name", "patient_name")) and not is_clinical_org_or_practitioner:
                    val = dict(val)
                    val["valueString"] = p_token

            # Special FHIR Patient resource handling
            if is_patient_resource:
                for k, v in val.items():
                    if k == "name":
                        new_dict[k] = [{"text": p_token, "family": p_token, "given": [p_token]}]
                    elif k == "address":
                        # Preserve district in FHIR address
                        dist = ""
                        if isinstance(v, list) and v and isinstance(v[0], dict):
                            dist = v[0].get("district", "")
                        addr_dict = {"city": "[VILLAGE]", "line": ["[ADDRESS]"], "postalCode": "[PIN]"}
                        if dist:
                            addr_dict["district"] = dist
                        new_dict[k] = [addr_dict]
                    elif k == "contact":
                        new_dict[k] = [{"name": {"text": "[RELATIVE_NAME]"}, "telecom": [{"value": "[PHONE]"}]}]
                    elif k == "identifier":
                        new_dict[k] = [{"value": id_token}]
                    elif k == "telecom":
                        new_dict[k] = [{"value": "[PHONE]"}]
                    elif k == "birthDate":
                        new_dict[k] = shift_date_string(str(v), delta_days) if delta_days else "[DOB]"
                    elif k == "text" and isinstance(v, dict):
                        new_dict[k] = {"status": v.get("status", "generated"), "div": f"<div xmlns=\"http://www.w3.org/1999/xhtml\">{p_token}, {id_token}</div>"}
                    else:
                        new_dict[k] = cls.clean_json_value(v, parent_key=k, secret=secret, pid=pid, delta_days=delta_days, known_names=known_names)
                return new_dict

            for k, v in val.items():
                clean_k = k
                # Key scrubber for patient_index.json
                if parent_key in ("by_uhid", "by_name") and secret:
                    h = hmac.new(secret, k.encode("utf-8"), hashlib.sha256).hexdigest()[:8]
                    clean_k = f"KEY_{parent_key}_{h}"
                elif MRN_RE.search(k):
                    clean_k = "[MRN_KEY]"

                # Metadata source_path scrubber
                if clean_k == "source_path" and isinstance(v, str):
                    new_dict[clean_k] = cls.clean_metadata_path(v)
                # Keep doctor and organization names intact in FHIR Practitioner and Organization
                elif is_clinical_org_or_practitioner and clean_k.lower() in ("name", "display", "text", "family", "given", "prefix"):
                    new_dict[clean_k] = v
                # Avoid redacting medical panel/test/rx names
                elif parent_key in ("panels", "tests", "results", "rx", "drugs", "diagnosis", "conditions") and clean_k.lower() == "name":
                    new_dict[clean_k] = v
                # Patient Index by_name values are MRNs
                elif parent_key == "by_name":
                    new_dict[clean_k] = id_token
                # FHIR identifier.value in Encounter/Condition/etc.
                elif clean_k.lower() == "value" and parent_key == "identifier":
                    new_dict[clean_k] = id_token
                elif clean_k.lower() in ("source", "reference") and isinstance(v, str):
                    new_dict[clean_k] = MRN_RE.sub(id_token, v)
                # District / State is preserved as quasi-identifier (with PII guard)
                elif clean_k.lower() in ("district", "distt", "dist", "jila", "zila", "state", "rajya", "pradesh"):
                    if isinstance(v, str) and (PHONE_RE.search(v) or MRN_RE.search(v) or AADHAAR_RE.search(v)):
                        new_dict[clean_k] = "[LOCALITY]"
                    else:
                        new_dict[clean_k] = v
                # Guardian / Relative names inside parent dict
                elif parent_key in ("guardian", "relative", "care_of", "contact") and clean_k.lower() in ("name", "first_name", "last_name", "full_name", "display", "text"):
                    new_dict[clean_k] = "[RELATIVE_NAME]"
                # Patient names
                elif any(k in clean_k.lower() for k in ("patient_name", "patient_full_name", "pat_name", "pt_name", "firstname", "first_name", "lastname", "last_name", "naam", "full_name")) or (clean_k.lower() in ("name", "patient", "pt") and not is_clinical_org_or_practitioner and not isinstance(v, dict)):
                    if isinstance(v, dict):
                        new_dict[clean_k] = {sub_k: p_token for sub_k in v}
                    elif isinstance(v, list):
                        new_dict[clean_k] = [p_token for _ in v]
                    else:
                        new_dict[clean_k] = p_token
                elif parent_key in ("subject", "patient") and clean_k.lower() == "display" and "Dr." not in str(v):
                    new_dict[clean_k] = p_token
                elif any(k in clean_k.lower() for k in ("guardian", "relative", "relative_name", "father", "husband", "spouse", "care_of", "care of", "c/o", "s/o", "w/o", "d/o", "pita/pati", "mother")) or clean_k.lower() in ("pita", "pati", "mata"):
                    if isinstance(v, dict):
                        new_dict[clean_k] = cls.clean_json_value(v, parent_key="guardian", secret=secret, pid=pid, delta_days=delta_days, known_names=known_names)
                    elif isinstance(v, list):
                        new_dict[clean_k] = ["[RELATIVE_NAME]" for _ in v]
                    else:
                        new_dict[clean_k] = "[RELATIVE_NAME]"
                elif any(x in clean_k.lower() for x in ("aadhaar", "aadhar")):
                    new_dict[clean_k] = "[AADHAAR]"
                elif any(x in clean_k.lower() for x in ("phone", "mobile", "callback", "contact_no", "ph_no", "mob", "cell", "telephone")):
                    new_dict[clean_k] = "[PHONE]"
                elif clean_k.lower() in ("email",):
                    new_dict[clean_k] = "[EMAIL]"
                elif any(k in clean_k.lower() for k in ("uhid", "mrn", "patientid", "pat_id", "patient_ref", "hospital_uhid", "reg_no", "ip_no", "op_no", "mr_no", "cr_no", "admission_no")):
                    new_dict[clean_k] = id_token
                elif clean_k.lower() in ("age", "age_yrs", "age_years", "umar", "aayu", "pat_age"):
                    new_dict[clean_k] = generalize_age(v)
                elif any(k in clean_k.lower() for k in ("birthdate", "birth_date", "birth_dt", "dob", "date_of_birth", "yob", "janm_tithi")):
                    new_dict[clean_k] = shift_date_string(str(v), delta_days) if delta_days else "[DOB]"
                elif clean_k.lower() in ("village", "gaon", "gram", "city", "post", "po", "post_office", "dak_ghar"):
                    new_dict[clean_k] = "[VILLAGE]"
                elif isinstance(v, str) and v.strip().lower() in GAZETTEER_VILLAGES:
                    new_dict[clean_k] = "[VILLAGE]"
                elif clean_k.lower() in ("mohalla", "locality", "tehsil", "taluk", "sub_district", "ward_no", "bed_no"):
                    new_dict[clean_k] = "[LOCALITY]"
                elif clean_k.lower() in ("pin", "zip", "pincode", "postalcode", "postal_code"):
                    new_dict[clean_k] = "[PIN]"
                elif clean_k.lower() in ("street", "house", "pata", "address", "address_line", "line", "makan"):
                    if isinstance(v, dict):
                        new_dict[clean_k] = cls.clean_json_value(v, parent_key="address", secret=secret, pid=pid, delta_days=delta_days, known_names=known_names)
                    elif isinstance(v, list):
                        new_dict[clean_k] = ["[ADDRESS]"]
                    else:
                        new_dict[clean_k] = "[ADDRESS]"
                # Free text clinical notes in JSON
                elif clean_k.lower() in ("notes", "complaints", "history", "remarks", "address_note", "reviewed_by", "div", "comment", "description") and isinstance(v, str):
                    new_dict[clean_k] = cls.clean_free_text(v, pid=pid, delta_days=delta_days, known_names=known_names)
                elif any(k in clean_k.lower() for k in ("collected", "visit_date", "visit_dt", "admission_date", "admission_dt", "discharge_date", "discharge_dt", "doa", "dod", "adm_date", "dis_date")) and isinstance(v, str) and delta_days:
                    new_dict[clean_k] = shift_date_string(v, delta_days)
                else:
                    new_dict[clean_k] = cls.clean_json_value(v, parent_key=clean_k, secret=secret, pid=pid, delta_days=delta_days, known_names=known_names)
            return new_dict

        if isinstance(val, list):
            if parent_key == "name":
                return [{"text": p_token, "family": p_token, "given": [p_token]}]
            return [cls.clean_json_value(x, parent_key=parent_key, secret=secret, pid=pid, delta_days=delta_days, known_names=known_names) for x in val]

        if isinstance(val, str):
            if delta_days and len(val) >= 8 and any(sep in val for sep in ("-", "/", ".")):
                val = shift_date_string(val, delta_days)
            return val

        return val

    @classmethod
    def clean_csv_header(cls, header: list[str]) -> list[str]:
        """Cleans CSV headers with sensitive patient columns."""
        clean = []
        for i, col in enumerate(header):
            c = col.strip()
            # If header embeds MRN e.g. "JJH-0077112 Zainab"
            if i > 0 and MRN_RE.search(c):
                clean.append(f"patient_{i}")
            else:
                clean.append(c)
        return clean

    @classmethod
    def clean_csv_row(
        cls,
        row: list[str],
        header: list[str],
        pid: str | None = None,
        delta_days: int = 0,
        known_names: set[str] | None = None,
    ) -> list[str]:
        """Sanitizes CSV row matching column semantic headers."""
        cleaned = []
        p_token = f"[{pid}]" if pid else "[PATIENT_NAME]"
        id_token = f"[{pid}]" if pid else "[MRN]"

        for i, val in enumerate(row):
            col = header[i].lower() if i < len(header) else ""

            # Preserve medical results, lab values, and billing amounts
            if any(k in col for k in ("result", "value", "unit", "range", "min", "max", "loinc", "test", "panel", "amount", "charge", "cost", "total", "claim", "approved", "time")):
                cleaned.append(val)
            # Preserve district as quasi-identifier (with PII guard)
            elif any(k in col for k in ("district", "distt", "dist", "jila", "zila", "state", "rajya", "pradesh")):
                if PHONE_RE.search(val) or MRN_RE.search(val) or AADHAAR_RE.search(val):
                    cleaned.append("[LOCALITY]")
                else:
                    cleaned.append(val)
            elif any(k in col for k in ("patient_name", "patient name", "pat_name", "pt_name", "patient_full_name", "firstname", "first_name", "lastname", "last_name", "naam", "full_name")) or col in ("name", "patient", "pt"):
                cleaned.append(p_token)
            elif any(k in col for k in ("guardian", "relative", "relative_name", "father", "husband", "spouse", "care_of", "care of", "c/o", "s/o", "w/o", "d/o", "pita/pati", "mother")) or col in ("pita", "pati", "mata"):
                cleaned.append("[RELATIVE_NAME]")
            elif any(k in col for k in ("phone", "mobile", "contact", "callback", "cell", "telephone", "mob", "ph_no", "contact_no")):
                cleaned.append("[PHONE]")
            elif any(k in col for k in ("aadhaar", "aadhar")):
                cleaned.append("[AADHAAR]")
            elif any(k in col for k in ("uhid", "mrn", "patientid", "pat_id", "reg_no", "ipd", "opd", "ip_no", "op_no", "mr_no", "cr_no", "admission_no")):
                cleaned.append(id_token)
            elif any(k in col for k in ("city", "village", "gaon", "gram", "post", "po", "post_office", "dak_ghar")):
                cleaned.append("[VILLAGE]")
            elif val.strip().lower() in GAZETTEER_VILLAGES:
                cleaned.append("[VILLAGE]")
            elif any(k in col for k in ("mohalla", "locality", "tehsil", "taluk", "ward_no", "bed_no")):
                cleaned.append("[LOCALITY]")
            elif any(k in col for k in ("pin", "zip", "pincode", "postalcode", "postal_code")):
                cleaned.append("[PIN]")
            elif any(k in col for k in ("street", "house", "pata", "address", "address_line", "line", "makan")):
                cleaned.append("[HOUSE]")
            elif col in ("age", "age_yrs", "age_years", "umar", "aayu", "pat_age"):
                cleaned.append(generalize_age(val))
            elif any(k in col for k in ("dob", "birthdate", "birth_date", "birth_dt", "date_of_birth", "yob", "janm_tithi")):
                cleaned.append(shift_date_string(val, delta_days) if delta_days else "[DOB]")
            elif any(k in col for k in ("admission", "discharge", "visit_date", "visit_dt", "date", "timestamp", "doa", "dod", "adm_date", "dis_date")):
                cleaned.append(shift_date_string(val, delta_days) if delta_days else val)
            else:
                cleaned.append(cls.clean_free_text(val, pid=pid, delta_days=delta_days, known_names=known_names))
        return cleaned
