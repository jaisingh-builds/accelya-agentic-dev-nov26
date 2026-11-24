> **Lab 4 fallback - real Bedrock output, unedited.** Draft 1 from the filled prompt (Claude Sonnet 4.5, rehearsal on 24 Nov 2026).
> Review it with day3/reviews/m3-brd-review.template.md exactly as you would your own draft. Synthetic scenario.

_model: us.anthropic.claude-sonnet-4-5-20250929-v1:0 | tokens in/out: 934/1768 | prompt: m3-brd-prompt.filled.md_

# Business Requirements Document: Ticket Issue File Validation

## Problem statement
Finance teams today settle ticket-issue files without systematic record-level validation. When agencies submit files containing invalid tickets—missing mandatory fields, mismatched airline prefixes, or future-dated issues—these errors are discovered late in the settlement cycle or not at all, causing reconciliation delays, manual rework, and financial exposure. Finance needs every incoming file validated record by record before settlement begins, with clear visibility into what passed, what failed, and why, so only clean tickets enter the settlement process.

## Scope

**In scope:**
- Record-by-record validation of daily ticket-issue CSV files against defined business and referential rules
- Routing records to validated, flagged, quarantine (by reason), or duplicate-skip outcomes
- Preservation of raw files exactly as received, immutable
- Structured logging of validation outcomes per file and per rule, with correlation identifiers
- Detection and handling of schema violations at file level and data violations at record level

**Out of scope:**
- Changes to agency file submission format, naming convention, or delivery schedule
- Curated zone population or reporting layer design (future phase)
- Automated correction or enrichment of invalid records
- Real-time validation during agency upload
- Historical re-validation of files received before this capability launches

## Functional requirements

**FR-1 File intake and immutability.**  
Every ticket-issue file arriving in the platform is stored in the raw zone exactly as received, with its original filename and timestamp, and is never modified or deleted.  
*Acceptance:* A file ticket_issue_6E_2026-11-25.csv uploaded at 02:17 IST appears in raw with that name, byte-identical to the source, and remains unchanged after validation completes.

**FR-2 Schema validation.**  
Before record processing begins, the platform checks that the file header matches the agreed column list (pnr, ticket_number, airline_code, fare_class, od_pair, issue_date, fare_amount, currency, agency_iata) in any order. A mismatch rejects the entire file to quarantine/schema with no record-level processing.  
*Acceptance:* A synthetic file with header "pnr,ticket_num,airline_code,..." lands in quarantine/schema, and the log shows zero records processed.

**FR-3 Mandatory field validation.**  
Each record must contain non-empty values for pnr, ticket_number, airline_code, fare_class, od_pair, issue_date, fare_amount, and currency. A record missing any mandatory field is routed to quarantine/required.  
*Acceptance:* In a 50-record synthetic file, 3 records with blank ticket_number land in quarantine/required; the log shows rule_id "required" and count 3.

**FR-4 Airline prefix referential check.**  
The first three digits of ticket_number must match a valid airline numeric code, and that code must correspond to the airline_code in the same record. A mismatch is routed to quarantine/referential.  
*Acceptance:* A record with ticket_number "239-1234567890" and airline_code "AI" (Air India, prefix 098) lands in quarantine/referential; the log cites rule_id "referential."

**FR-5 Issue date business rule.**  
If issue_date is after the file's business date (extracted from filename), the record is routed to quarantine/business. If issue_date is more than 30 calendar days before the file's business date, the record is routed to validated and flagged with "late_issue."  
*Acceptance:* In ticket_issue_6E_2026-11-25.csv, a record dated 2026-11-26 → quarantine/business; a record dated 2026-10-20 → validated with late_issue flag; the log shows both outcomes and counts.

**FR-6 Duplicate ticket detection.**  
If the same ticket_number appears more than once in a single file, the platform keeps the first occurrence (subject to all other validations) and skips subsequent occurrences, counting them as duplicates but not quarantining them.  
*Acceptance:* A synthetic file with ticket_number "098-5555555555" on rows 12 and 89: row 12 is validated or quarantined per its data quality; row 89 is skipped; the log shows duplicate_count 1.

**FR-7 Record routing.**  
Each record goes to exactly one outcome: validated, validated-with-flag, quarantine/<reason>, or skipped as duplicate. The sum of these outcomes equals the input record count (excluding header).  
*Acceptance:* For ticket_issue_AI_2026-11-25.csv with 100 records: validated + flagged + quarantined + skipped = 100, and every quarantined record is tagged with its rule_id.

**FR-8 Validation summary logging.**  
At the end of processing each file, the platform writes a structured JSON log record containing correlation_id, filename, business_date, total_records, validated_count, flagged_count, quarantine_count (by reason), duplicate_count, and processing timestamp. No fare amounts, PNR values, or ticket numbers appear in logs.  
*Acceptance:* The log for ticket_issue_UK_2026-11-25.csv shows `{"correlation_id":"...","filename":"...","validated_count":9512,"quarantine_required_count":23,...}` with no sensitive data.

## Non-functional requirements

- **Correctness:** Every record is evaluated against all applicable rules; no record is lost, double-counted, or routed to the wrong outcome.
- **Security:** Access to quarantine zones is restricted to finance and data operations roles; raw and validated zones follow least-privilege access policies.
- **Observability:** Every file processed emits a summary log; every validation failure is traceable to a specific rule and correlation identifier.
- **Scalability:** The platform handles up to 40 files per day, each up to 100,000 records, with most arriving between 00:00–03:00 IST, completing validation before business hours.
- **Cost:** Storage and compute resources scale with daily file volume; no standing infrastructure runs outside the nightly processing window.

## Data handling

- **Raw zone:** Stores every file exactly as received, immutable, retained for 7 years per financial audit requirements.
- **Validated zone:** Stores records that passed all validation rules, plus records flagged (e.g., late_issue), partitioned by airline and business date, retained for 7 years.
- **Quarantine zones (schema, required, referential, business):** Store rejected records with rule_id and correlation_id, retained for 90 days; accessible to finance operations and data stewards for investigation and agency follow-up.
- **Curated zone:** Design deferred to reporting phase; will source from validated zone.

## Open questions for the business

1. Should re-sent files (same airline, same business date) replace the previous version in validated/quarantine, or be processed as a separate versioned submission?
2. Who is authorized to mark quarantined records as "accepted with override" and move them to validated after manual review?
3. What is the escalation process when an entire file is rejected due to schema mismatch—does the agency receive automated notification?
4. Are there additional referential checks required (e.g., fare_class must exist in a master list, od_pair must be a valid route)?
5. Should the platform enforce a maximum file age (e.g., reject files older than 48 hours from their business date)?
