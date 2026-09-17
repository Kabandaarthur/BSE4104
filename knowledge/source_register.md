# Corpus / Source Register

**Project:** University Student-Support Case Agent (BSE4104) — Week 3 deliverable 1

> **Generated file — do not edit by hand.**
> Source of truth is [`knowledge/corpus.json`](corpus.json).
> Regenerate with `python src/fetch_corpus.py --register`.

**Indexed documents:** 11 (9 real, 2 synthetic) &nbsp;|&nbsp; **Excluded:** 5 &nbsp;|&nbsp; **Total extracted words:** 67,752

Every document below was logged here *before* being chunked and indexed. Each row's SHA-256 is taken over the exact bytes retrieved from the URL, so the indexed text can be proven to match what the source served on the retrieval date.

## Indexed documents

| Doc ID | Title / Description | Source / Provenance | Real or Synthetic | Date Added |
|---|---|---|---|---|
| D03 | **Rules on Examination Malpractices and Irregularities (2019 amendment)** — Approved amended rules governing examination malpractice, irregularities and associated penalties for students. | [https://policies.mak.ac.ug/sites/default/files/policies/Approved-Amended-Rules-on-Examination-Malpractices-and-Irregularities-March-2019.pdf](https://policies.mak.ac.ug/sites/default/files/policies/Approved-Amended-Rules-on-Examination-Malpractices-and-Irregularities-March-2019.pdf)<br>Listed at: [policy index](https://policies.mak.ac.ug/policy/rules-examination-malpractice-and-irregularities-students)<br>17 pages, 5,795 words<br>`sha256:0314a554a10918f6…` | Real | 2026-09-14 |
| D04 | **Examinations Information (ID requirements, exam-day policy FAQ)** — Student-facing examination information page. | [https://mak.ac.ug/node/108](https://mak.ac.ug/node/108)<br>2,271 words<br>`sha256:c9332bcbfa0da96f…` | Real | 2026-09-14 |
| D05 | **Makerere University Academic Calendar** — Semester dates, orientation, examination periods. | [https://mak.ac.ug/students/academic-calendar](https://mak.ac.ug/students/academic-calendar)<br>1,496 words<br>`sha256:db057a15c8f78d81…` | Real | 2026-09-14 |
| D06 | **CoCIS Academic Calendar** — College-specific academic dates for the College of Computing and Information Sciences. | [https://cocis.mak.ac.ug/academics/academic-calendar/](https://cocis.mak.ac.ug/academics/academic-calendar/)<br>1,414 words<br>`sha256:03ecd002b721b931…` | Real | 2026-09-14 |
| D07 | **CoCIS Undergraduate Programmes** — Entry requirements, credit and graduation load for CoCIS undergraduate programmes. | [https://cocis.mak.ac.ug/academics/academic-programs/undergraduate-programs/](https://cocis.mak.ac.ug/academics/academic-programs/undergraduate-programs/)<br>57 words<br>`sha256:c100eb1d22b894ea…` | Real | 2026-09-14 |
| D08 | **Department Query / Case Escalation Procedure** — Internal procedure describing how a student query is received, triaged, escalated and closed. | Team-created. `knowledge/synthetic/D08.md`<br>747 words<br>`sha256:3cdb120e15301959…` | **Synthetic — clearly labelled** | 2026-09-16 |
| D09 | **Sample Student Support Case Records** — Fabricated support case records used to exercise case_status scenarios. | Team-created. `knowledge/synthetic/D09.md`<br>471 words<br>`sha256:341e0227e0d92ee3…` | **Synthetic — clearly labelled** | 2026-09-16 |
| D11 | **Makerere University Academic Policies Manual (Revised 2025)** — Consolidated academic policy manual. | [https://policies.mak.ac.ug/sites/default/files/policies/Makerere-Academic-Policies-Manual-Revised-Version-2025.pdf](https://policies.mak.ac.ug/sites/default/files/policies/Makerere-Academic-Policies-Manual-Revised-Version-2025.pdf)<br>Listed at: [policy index](https://policies.mak.ac.ug/policy/makerere-university-academic-policies-manual-2025)<br>178 pages, 50,578 words<br>`sha256:cd6b6aaccce41cc6…` | Real | 2026-09-16 |
| D13 | **Policy on Remarking Students' Work and Retention of Scripts** — Procedure and eligibility for requesting a remark, and how long scripts are retained. | [https://policies.mak.ac.ug/sites/default/files/policies/Policy_on_remarking_students_work_and_retention_of_scripts.pdf](https://policies.mak.ac.ug/sites/default/files/policies/Policy_on_remarking_students_work_and_retention_of_scripts.pdf)<br>Listed at: [policy index](https://policies.mak.ac.ug/policy/remarking-students-work-and-retention-scripts)<br>1 pages, 371 words<br>`sha256:a6c04af09f86c561…` | Real | 2026-09-16 |
| D14 | **Guidelines for Field Attachment** — Internship/field attachment requirements, supervision and assessment. | [https://policies.mak.ac.ug/sites/default/files/policies/GUIDELINES_FOR_FIELD_ATTACHMENT.pdf](https://policies.mak.ac.ug/sites/default/files/policies/GUIDELINES_FOR_FIELD_ATTACHMENT.pdf)<br>Listed at: [policy index](https://policies.mak.ac.ug/policy/guidelines-field-attachment)<br>19 pages, 4,540 words<br>`sha256:aa1a7e1eaaf82a93…` | Real | 2026-09-16 |
| D16 | **Makerere University Students Portal (landing page)** — Index of student-facing services and links. | [https://mak.ac.ug/students](https://mak.ac.ug/students)<br>12 words<br>`sha256:cfc93020c9782d8d…` | Real | 2026-09-16 |

## Excluded / unavailable sources

Recorded deliberately: the register should show what was attempted, not only what succeeded.

| Doc ID | Title | Attempted source | Why excluded |
|---|---|---|---|
| D01 | Makerere University Students' Regulations (2015) | `https://policies.mak.ac.ug/sites/default/files/policies/Makerere-University-Students-Regulations-2015.pdf` | Scan - no text layer (16 pages, 0 extractable characters). See note below the table. |
| D02 | Makerere University Regulations (Dean of Students web/HTML version, by section) | `https://dos.mak.ac.ug/regulations/makerere-university-regulations` | Host `dos.mak.ac.ug` does not resolve in DNS (checked 2026-09-16, apex and www). Duplicated D01's content; D10 was sourced as the substitute. |
| D10 | Makerere University Revised Regulations: Semester / Credit System for Undergraduates | `https://policies.mak.ac.ug/sites/default/files/policies/Makerere-Revised-Regulations-Semester-Credit-for-Undergraduates.pdf` | Scan - no text layer (19 pages, 0 extractable characters). See note below the table. |
| D12 | Makerere University Policy on Students' Accommodation | `https://policies.mak.ac.ug/sites/default/files/policies/Makerere-Students-Accommodation-Policy.pdf` | Scan - no text layer (9 pages, 0 extractable characters). See note below the table. |
| D15 | Makerere University Policy on Persons Living With Disabilities | `https://policies.mak.ac.ug/sites/default/files/policies/Makerere-Policy-on-Persons-Living-With-Disabilities.pdf` | Scan - no text layer (25 pages, 0 extractable characters). See note below the table. |

**On the four scans.** D01, D10, D12 and D15 are image-only PDFs: every page is a
single scanned picture, so a text extractor returns nothing at all. They are genuine
Makerere documents and were retrieved successfully — the checksums in
`knowledge/corpus.json` prove what was downloaded — but they cannot be indexed or
cited as they stand. OCR is implemented in `src/fetch_corpus.py` (`ocr_pdf`, with
two-column handling) and recovers them; it was not run for this submission because it
needs roughly 15-20 minutes of sustained CPU. To index them later:

```bash
python src/fetch_corpus.py --only D01 --force   # repeat for D10, D12, D15
```

Their raw scans are not committed (24 MB of page images for zero retrievable text);
re-fetch from `source_url` and check the recorded sha256.

## Provenance notes

- Raw downloads are committed under `knowledge/raw/` (except the four scans above);
  extracted text under `knowledge/text/`.
- `python src/fetch_corpus.py --verify` re-checksums every file and exits non-zero on drift.
- Synthetic documents (D08, D09) carry a visible banner **inside the file body**, not only in this
  table, so a retrieved chunk cannot be mistaken for real Makerere policy.
- Calendar pages (D05, D06) are time-sensitive; checksum drift on those is expected between
  semesters and means *re-fetch*, not *corruption*.
- No real student data is stored anywhere in this corpus.

_Generated 2026-09-17 14:53 UTC from `knowledge/corpus.json`._
