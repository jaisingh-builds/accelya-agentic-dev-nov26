> **Lab 4 fallback, draft 2 - real Bedrock output, unedited.** Produced from draft 1 plus the five revision notes below (same model, same rehearsal).
> Check each note was applied, finish the draft by hand, and re-score it. Synthetic scenario.
>
> Revision notes used:
> 1. FR-2: the header must match the agreed columns exactly, in order. Remove "in any order".
> 2. Add a rerun requirement: processing the same file (same name, same bytes) again gives identical outcomes and creates no second set of outputs, so nothing is settled twice.
> 3. FR-8: use only keys allowed by docs/LOGGING_CONTRACT.md (ts, level, service, correlation_id, boundary, message, key, record_count, ...); don't invent new log keys. Put per-file counts by outcome in a manifest stored next to the outputs, and make the manifest a requirement.
> 4. Retention periods and who may read quarantine were not given by the business: remove the invented values (7 years, 90 days, named roles) and add them as open questions.
> 5. Make every NFR testable. Where the business gave no number (e.g. "before business hours"), write it as an open question instead of a vague phrase.

_model: us.anthropic.claude-sonnet-4-5-20250929-v1:0 | tokens in/out: 1126/1744 | prompt: m3-brd-prompt.filled-v2.md_

# Business Requirements Document: Ticket Issue File Validation

## Problem statement

Today, Accelya's finance team settles ticket-issue files from agencies without record-level validation. When invalid tickets—missing fields, wrong airline codes, or future issue dates—reach settlement, finance must manually investigate and reconcile discrepancies after payment has been initiated. The business requires automated validation before settlement so that only clean tickets are processed, rejected records are quarantined with clear reasons, and finance can audit what was accepted or rejected per file.

## Scope

**In scope:**
- Record-by-record validation of ticket-issue CSV files against agreed business and referential rules
- Routing records to validated, validated-with-flag, quarantine (by reason), or skipped-duplicate outcomes
- Storing raw files unchanged, validated records, and quarantined records in separate zones
- Producing a manifest per file listing counts by outcome and quarantine reason
- Logging that respects the agreed logging contract (correlation_id, no fare or PII)
- Idempotent reprocessing: identical file yields identical outcomes without duplicate outputs

**Out of scope:**
- Changes to agency file format, naming convention, or delivery schedule
- Curated zone population (reporting layer, later phase)
- Real-time or streaming validation (batch only, once per business day per file)
- Correction or resubmission workflow for quarantined records

## Functional requirements

**FR-1 File intake.** The platform accepts ticket-issue files named `ticket_issue_<airline>_<YYYY-MM-DD>.csv` and stores each file exactly as received in the raw zone, preserving original bytes and filename.  
*Acceptance:* synthetic file `ticket_issue_AI_2026-11-25.csv` appears in raw with identical checksum to the uploaded file.

**FR-2 Schema validation.** A file whose header does not match the agreed column list (pnr, ticket_number, airline_code, fare_class, od_pair, issue_date, fare_amount, currency, agency_iata) exactly and in order is rejected whole and routed to quarantine/schema with no records processed.  
*Acceptance:* file with header `pnr,ticket_number,airline_code,extra_column,...` produces zero validated records and one entry in quarantine/schema.

**FR-3 Required field validation.** Every record missing any of pnr, ticket_number, airline_code, fare_class, od_pair, issue_date, fare_amount, or currency is routed to quarantine/required.  
*Acceptance:* record with empty ticket_number appears in quarantine/required; record with all required fields present does not.

**FR-4 Referential validation.** A record whose ticket_number 3-digit prefix does not belong to the issuing airline (airline_code) is routed to quarantine/referential.  
*Acceptance:* ticket_number `176-1234567890` in file `ticket_issue_AI_2026-11-25.csv` (airline AI = prefix 098) is quarantined; prefix `098-1234567890` is not.

**FR-5 Business rule validation.** A record whose issue_date is after the file's business date (extracted from filename) is routed to quarantine/business.  
*Acceptance:* issue_date `2026-11-26` in file `ticket_issue_AI_2026-11-25.csv` is quarantined.

**FR-6 Late-issue flagging.** A record whose issue_date is more than 30 days before the file's business date is routed to validated with a `late_issue` flag.  
*Acceptance:* issue_date `2026-10-20` in file `ticket_issue_AI_2026-11-25.csv` (36 days prior) appears in validated with flag; issue_date `2026-10-26` (29 days prior) appears in validated without flag.

**FR-7 Duplicate detection.** When the same ticket_number appears more than once in a single file, the first occurrence is processed normally and subsequent occurrences are skipped and counted.  
*Acceptance:* file with ticket_number `098-1234567890` on rows 10 and 50 produces one validated or quarantined record and manifest shows `skipped_duplicate_count: 1`.

**FR-8 Outcome routing.** Each record goes to exactly one outcome: validated, validated-with-flag, quarantine/<reason>, or skipped-duplicate, and the sum of all outcomes equals the input record count (excluding header).  
*Acceptance:* file with 42 records produces counts where validated + flagged + quarantined + skipped = 42, and every quarantined record identifies its rule.

**FR-9 Manifest generation.** For each processed file, a manifest JSON document is stored alongside outputs listing filename, business_date, correlation_id, record_count (input), validated_count, flagged_count, quarantined_count (by reason: schema, required, referential, business), and skipped_duplicate_count.  
*Acceptance:* `ticket_issue_AI_2026-11-25.csv` produces manifest with all counts matching actual output record counts.

**FR-10 Idempotent reprocessing.** Processing the same file (identical name and bytes) a second time produces identical outcomes and does not create duplicate output records or manifests.  
*Acceptance:* uploading `ticket_issue_AI_2026-11-25.csv` twice results in one set of validated/quarantined outputs and one manifest; second run logs indicate file already processed.

## Non-functional requirements

- **Correctness:** Every record is validated against all applicable rules; no record is settled without passing validation.
- **Security:** Only authorized finance and operations personnel may read quarantine zones; raw and validated zones follow existing platform access controls.
- **Observability:** Every log record is a single JSON object containing ts, level, service, correlation_id, boundary, message, and permitted keys (key, record_count); logs never contain fare_amount, pnr contents, or passenger data.
- **Scalability:** The platform processes up to 40 files per day, each up to 100,000 records, completing validation before business hours begin.
- **Cost:** Storage and compute resources scale with daily file volume; no resources run idle outside processing windows.

## Data handling

- **Raw zone:** Stores every received file exactly as uploaded, never modified. Filename and checksum preserved.
- **Validated zone:** Stores records that passed all validation rules, and records flagged (e.g., late_issue) but otherwise valid.
- **Quarantine zone:** Stores rejected records partitioned by reason (schema, required, referential, business), each record annotated with rule identifier and correlation_id.
- **Manifest:** Stored with validated outputs; contains per-file counts by outcome and correlation_id.

## Open questions for the business

1. What is the required retention period for raw, validated, and quarantine zones?
2. Which roles or teams are authorized to read quarantine data?
3. What is the deadline (IST) by which all files arriving 00:00–03:00 must complete validation ("before business hours")?
4. Should reprocessing a corrected file with the same name but different bytes replace prior outputs, or is this a new submission with a new business date?
5. Are there additional referential rules (e.g., valid fare_class codes, od_pair format) to be applied in this phase?
