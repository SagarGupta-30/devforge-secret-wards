# Secret Wards Privacy Firewall (Track 3) — DevForge
**Team:** [`devforge-secret-wards`](https://github.com/SagarGupta-30/devforge-secret-wards) · **Status:** `100% Offline • CPU-Only • LLM-Free • CONTRACT OK`

A high-performance, deterministic healthcare privacy firewall built for Track 3 ("Secret Wards"). De-identifies heterogeneous, multilingual electronic health records across English, Hindi (Devanagari), and Hinglish in sub-second time without sacrificing medical and epidemiological utility.

---

## 1. Overview & Architecture

The firewall processes heterogeneous clinical data dumps across CSVs, TSVs, JSON/FHIR resources, and free-text clinical notes, redacting personal identifiers across six structural channels (`content`, `filename`, `base64`, `metadata`, `key`, `header`) while preserving 100% of medical and clinical data integrity (medicines, dosages, frequencies, vitals, lab results, and hospital stay durations).

```
Input Files ──► [ Pass 1: Candidate Extraction & Record Linkage ]
                     │  ├── Cross-Script Normalization (ITRANS)
                     │  ├── Phonetic & Multi-Signal Clustering
                     │  └── Deterministic Keyed Pseudonyms (PSN-XXXXXX)
                     ▼
                [ Pass 2: Ingestion, Shielding & Sanitization ]
                     │  ├── Ingestion & Strict Quarantine (Parser Safe)
                     │  ├── Filename Channel HMAC Scrubber
                     │  ├── Clinical Shield (Pass A: Mask Medical Data)
                     │  ├── PII Extraction (Pass B: All Channels & Scripts)
                     │  ├── Privacy Engine (Per-Patient Date Shift & Age Banding)
                     │  └── Clinical Shield (Pass C: Restore Medical Data)
                     ▼
Output Release ──► sanitized/ + records.jsonl + report.json
```

---

## 2. Key Modules & Implementation

### 1. Ingestion & Strict Quarantine (`src/ingestion.py`)
- **Encoding Repair**: Reliably detects and decodes UTF-8 (with/without BOM), UTF-16 LE/BE, and Windows cp1252.
- **Fail-Safe Quarantine**: Flags and isolates hazardous files without copying raw bytes to `sanitized/`:
  - Unsupported file formats (`.xml`, `.pdf`, `.jpg`, `.png`, `.doc`, etc.)
  - Empty (0-byte) files
  - Corrupted byte sequences failing decoding
  - Extension mismatches (e.g. CSV files containing JSON payloads)
  - Malformed syntax or deeply nested JSON (nesting depth > 100 recursion bombs)
- **Zero-Leak Filename Channel Scrubber**: Detects filenames containing MRNs, dates, phone numbers, or capitalized person names, deterministically renaming them using `HMAC-SHA256(secret, rel)[:10]` (e.g., `doc_bc782a6092.txt`), updating both `report.json` and `records.jsonl`.
- **Zero-Leak Quarantine Reporting**: Quarantine entries in `report.json` reference files strictly by SHA-256 hash with generic classification reasons, preventing quarantine logs from leaking filenames.

### 2. Clinical Shield (`src/clinical_shield.py`)
- Protects medical entities before PII extraction using immutable placeholders (`__CLIN_XXXX__`) and restores them post-sanitization:
  - **Clinicians & Practitioners**: Preserves `Dr. <Name>` and credentialed staff (`Dr. Ramesh Srivastava (MD Medicine)`), distinguishing doctors from patients.
  - **Symptoms & Conditions**: Preserves complaints (`C/O fever`, `H/O TB`, `K/C/O DM2`) and Devanagari conditions (`बुखार`, `खांसी`, `दर्द`, `कमजोरी`).
  - **Medical Eponyms**: Protects conditions named after persons (`Addison's`, `Parkinson's`, `Alzheimer's`, `Hodgkin's`, `Cushing's`, `Bell's`, `Graves'`, `Crohn's`, `Hashimoto's`, `Kawasaki`).
  - **Medications & Dosages**: Indian formulations, brand names, and generics (`Dolo`, `Crocin`, `Paracetamol`, `Augmentin`, `Azithral`, `Pan-D`, `Pantop`, `Ramipril`, `Telma`, `Metformin`, `Atorva`) along with morphological drug suffixes (`-mab`, `-nib`, `-pril`, `-sartan`, `-olol`, `-statin`, `-mycin`, `-cillin`, `-floxacin`, `-prazole`, `-formin`, `-gliptin`, `-tidine`, `-zepam`, `-fenac`, `-mol`), strengths (`450 mg + 300 mg`), and schedules (`1-0-1`, `1-1-1`, `0-0-1`, `BD`, `TDS`, `OD`, `SOS`, `HS`, `x 5 days`).
  - **Vitals & Labs**: Blood pressure (`BP 117/82`), heart rate, pulse, temperature, SpO2, blood sugar (`RBS`, `FBS`), HbA1c, units (`mg/dL`, `g/dL`, `mmol/L`, `/cumm`, `IU/L`).
  - **Hospital Wards**: Hospital specialty units (`Ward 4 (Medicine)`, `Ward 8 (Surgery)`, `Bed 24`, `ICU`, `CCU`, `NICU`, `PICU`).

### 3. All-Channel PII Extractor (`src/pii_extractor.py`)
- Sanitizes across all 6 data channels:
  - **Content**: Free text clinical summaries, OPD notes, nursing notes, prescriptions, and chats in Latin, Devanagari, and Hinglish.
  - **Filename**: Scrubbed deterministically via HMAC.
  - **Metadata**: Sanitizes directory and author paths in document metadata.
  - **Base64**: Decodes attachments and binary data, scrubs nested PII, and re-encodes back to base64.
  - **Keys**: Scrubs dictionary keys (e.g. `patient_index.json` UHID/Name keys) using HMAC digests.
  - **Headers**: Cleans CSV headers embedding MRNs or patient identifiers (`misc/ward_vitals_sheet.csv`).
- **Devanagari & Hinglish Awareness**:
  - Handles Hindi honorifics (`जी`, `साहब`, `साहिब`, `दीदी`, `भैया`, `बाबू`, `बाबूजी`, `अम्मा`).
  - Handles Hindi accusative/genitive constructs (`<Name> को बुखार है`, `<Name> ka BP 111/78`).
  - Structured labels: `नाम`, `पिता/पति का नाम`, `पंजीकरण सं.`, `आईपीडी सं.`, `मोबाइल`, `जन्म तिथि`, `पता`, `ग्राम`, `तहसील`, `मोहल्ला`.

### 4. Cross-Script Record Linker (`src/record_linker.py`)
- **Profile Extraction**: Extracts candidate profiles from CSV/TSV, JSON (OPD, DHH, Lab, FHIR), and text notes.
- **Cross-Script Normalization**: Transliterates Devanagari names using `indic-transliteration` ITRANS canonicalized into normalized phonetics (e.g. `"मोहम्मद आरिफ"` -> `"mohammad arif"`).
- **Phonetic Canonicalization**: Expands common prefixes (`md`, `mohd`, `mohammed` -> `mohammad`), strips honorifics and titles (`smt`, `shri`, `kumari`, `pt`, `mr`, `mrs`), and applies Double Metaphone keys (`jellyfish`).
- **Multi-Signal Clustering**:
  - Deterministic union-find on hard identifiers (`MRN/UHID`, `Phone`, `Aadhaar`).
  - Secondary confirmation via token sort ratio (`>= 85`) corroborated with matching relative names or villages.
  - Strict anti-over-merge guardrails: ambiguous or isolated mentions are assigned `"patient": null` to preserve **1.00 linking precision**.
- **Keyed Pseudonyms**: Mints 6-character HMAC pseudonyms (`PSN-XXXXXX`) using `RAKSHA_SECRET` and canonical cluster seeds.

### 5. Privacy Engine & Quasi-Identifier Defense (`src/privacy_engine.py`)
- **Interval-Preserving Date Shifting**: Computes a per-patient integer offset in `[-30, +30]` (non-zero) keyed on `RAKSHA_SECRET`. Shifts all dates in a patient's timeline (admission, discharge, lab timestamps, prescription dates, birth dates) by the exact same delta:
  $$\text{Stay Duration}_{\text{sanitized}} = (T_{\text{dis}} + \Delta) - (T_{\text{adm}} + \Delta) = T_{\text{dis}} - T_{\text{adm}} = \text{Stay Duration}_{\text{raw}}$$
  Bed utilization and epidemiological stay durations remain **100% mathematically exact**, while calendar-based voter-list correlation attacks fail completely.
- **Format Preservation**: Preserves original format and delimiters for `YYYY-MM-DD`, `DD/MM/YYYY`, `MM/DD/YYYY`, `DD-MM-YYYY`, `DD.MM.YYYY`, and ISO-8601 timestamps.
- **Age Banding**: Standardizes patient ages into 5-year bands (`50-54`), top-coding ages >= 90 to `90+`.
- **Location Generalization**: Masks house numbers, landmarks, and village names with generic placeholders (`[HOUSE]`, `[LANDMARK]`, `[VILLAGE]`, `[LOCALITY]`, `[PIN]`) while preserving broad administrative districts (`Rae Bareli`, `Barabanki`, `Sitapur`, `Hardoi`, `Unnao`) as required for geographic health surveillance.

---

## 3. Empirical Verification & Self-Check Results

Evaluated against the official practice benchmark dataset (`check_dev.py` and `check_submission.py`):

| Evaluation Metric | Baseline / Practice Target | Secret Wards (Raksha) | Impact |
|---|---|---|---|
| **Personal Data Leaked** | 3,230 leaks (100%) | **0 / 3,230 (0.0% Leaked)** | 100% redaction across Latin, Deva & Hinglish |
| **Planted Canary Leaks** | 4 / 4 leaked (-12 pts) | **0 / 4 Leaked (Zero Leaks)** | Canary tokens completely eliminated |
| **Medical Data Kept** | Variable (< 60%) | **4,855 / 4,855 (100.0%)** | 0 lost clinical terms (vitals, drugs, labs intact) |
| **Record Linking F0.5** | 0.00 (Unlinked) | **P: 1.00 \| R: 0.94 \| F0.5: 0.99** | 765 linked records; **0 False Merges** |
| **Quarantine Handling** | Crash / Raw copies | **8 / 8 Quarantined (100%)** | Zero raw copies; parser protected via SHA-256 vault |
| **Runtime & Peak RAM** | Unbounded / slow | **0.76s \| 44.5 MB RAM** | Lightweight CPU execution, ideal for rural clinics |
| **Output Contract** | Invalid schema | **CONTRACT OK** | Valid `raksha-report/1`, clean UTF-8 outputs |
| **Submission Check** | Blocked | **READY TO SUBMIT** | Fully automated pre-submit verification passed |

---

## 4. Stage 2 Live Demo Console (`ui.py`)

A 100% offline, dark-mode Streamlit dashboard (`ui.py`) engineered for the Stage 2 Desk Review live demo:

- **Top KPI Strip**: Realtime metrics for Files Sanitized (`192 / 200`), Quarantined Files (`8`), Canary Leaks (`0 / 4`), Medical Fidelity (`100.0%`), Speed (`0.76s`), and Peak Memory (`44.5 MB`).
- **Tab 1 — Multilingual Clinical Redaction Diff**: Side-by-side comparison of raw input files vs. sanitized outputs across CSVs, JSON/FHIR, and Devanagari/Hindi text notes with visual badge highlighting (`[PSN-XXXXXX]` in cyan, `[REDACTED_*]` in amber, preserved vitals/meds in green) and HMAC-SHA256 filename scrub detection.
- **Tab 2 — Cross-Hospital Identity Resolution Explorer**: Unified patient trajectory explorer across all 59 patient clusters, displaying cross-facility care encounters alongside our dedicated **Anti-Over-Merge Safety Rationale** for the 51 unlinked records (`"patient": null`).
- **Tab 3 — Voter-List Re-Identification Defense & Quarantine Vault**:
  - **Quasi-Identifier Attack Simulator**: Interactive cohort filters (District, Age Band, Stay Duration) contrasting vulnerable raw records ($k=1$, direct voter roll linkage) against Secret Wards protected records ($k \ge 450$).
  - **Stay Duration Invariance Proof**: Demonstrates $(T_{\text{dis}} + \Delta) - (T_{\text{adm}} + \Delta) = T_{\text{dis}} - T_{\text{adm}}$, proving 100% clinical research utility.
  - **Quarantine Vault**: Triage table displaying all 8 quarantined files identified strictly by SHA-256 hash with specific vulnerability mitigation rationales (recursion bombs, corrupt bytes, binary injection) and zero filename exposure.

---

## 5. Executive Brief & Setup

### Project Brief PDF & Repository
The official 2-page project brief is frozen at the repository root:
- **`Project_Brief.pdf`**: Strictly 2 pages A4 adhering to the official Secret Wards template.
- Built via: `python3 scripts/build_brief.py Project_Brief.pdf`
- **GitHub Repository**: [`https://github.com/SagarGupta-30/devforge-secret-wards`](https://github.com/SagarGupta-30/devforge-secret-wards)

### Execution Commands

```bash
# 1. Setup environment
pip install -r requirements.txt

# 2. Run firewall on dataset
RAKSHA_SECRET=practice python3 main.py --input ~/Secret-Wards-ToolKit/dev/input --output my_output

# 3. Validate output contract
python3 ~/Secret-Wards-ToolKit/validate_output.py --input ~/Secret-Wards-ToolKit/dev/input --output my_output

# 4. Run self-check benchmark
python3 ~/Secret-Wards-ToolKit/check_dev.py --output my_output

# 5. Run pre-submission verification
python3 ~/Secret-Wards-ToolKit/check_submission.py --repo . --run

# 6. Launch Stage 2 Live Demo Console
streamlit run ui.py -- --output my_output --input ~/Secret-Wards-ToolKit/dev/input
```
