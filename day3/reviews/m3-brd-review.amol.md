# HITL_REVIEW - m3 BRD

**Artefact:** `day3/samples/m3-brd-sample-v1.md` (draft `v1`)
**Reviewer:** `amol`   **Date:** `2026-11-26`

## BRD checks (tick each; every unticked item becomes a revision note)

- [x] Every functional requirement has a testable acceptance criterion (a number, a file, a route)
- [x] Record-level routing is stated: validated / quarantine by reason / flagged / skipped duplicate
- [x] Whole-file rejection (header drift) is a requirement, not an afterthought
- [ ] The four repeats have requirements: duplicate delivery, same-content resend, duplicate ticket row, deliberate rerun after a rule fix
- [ ] Reruns are reproducible: dates relative to the file; source, rules, reference data and code versions recorded
- [ ] Audit: raw is kept unchanged; every output can be traced to its source file and line
- [x] Observability NFR names the logging contract (docs/LOGGING_CONTRACT.md): JSON, correlation_id, no payloads
- [x] No implementation choices (no service names, no code) - that's Day 4's design
- [ ] Unknowns are open questions, not invented requirements
- [x] Airline vocabulary (pnr, ticket_number, fare_class, od_pair), no order_id / customer

## Score (0-3 per dim - PASS = >=12/15 AND every dim >=2)

| Dim | Score | Evidence (one line) |
|---|---|---|
| Correctness (complete, testable, true to the BR) | 2 | FR-1 to FR-8 cover the main validation flow with acceptance criteria, but no rerun/idempotency requirement and no manifest |
| Security (PII handling, access to quarantine, no data in logs) | 2 | NFR names no-payload logging and quarantine access restriction, but invents role names (finance, data stewards) the business did not give |
| Observability (logging contract + manifest as requirements) | 2 | FR-8 describes summary logging with correct keys, but invents log keys not in LOGGING_CONTRACT.md (validated_count, quarantine_required_count) and no manifest requirement |
| Scalability (volume, peaks, reruns, late and duplicate files) | 2 | NFR states 40 files/day up to 100k records and nightly window, but no rerun or duplicate-delivery handling requirement |
| Cost (retention per zone, no over-specification) | 1 | Invents retention periods (7 years raw, 90 days quarantine) and named roles not given by business - should be open questions |
| **TOTAL** | 9/15 | |

**Verdict:** REVISE

## Revision notes for v2 (paste these into the prompt's REVISION section)
1. Add a rerun/idempotency requirement (FR): processing the same file (same name, same bytes) a second time must produce identical outcomes and not create duplicate outputs - so nothing is settled twice.
2. FR-8 logging: use only keys allowed by docs/LOGGING_CONTRACT.md (ts, level, service, correlation_id, boundary, message). Put per-file counts by outcome in a manifest stored next to the outputs, and make the manifest a separate functional requirement.
3. Remove invented retention periods (7 years for raw, 90 days for quarantine) and named roles (finance, data stewards) from Data handling section - the business did not give these. Add them as open questions instead.
4. Add duplicate-delivery and resend as requirements: when S3 delivers the same event twice, or the same file is re-uploaded with the same correlation-id, the system must detect and handle it (log it, not double-process).
5. FR-2 schema check says header columns match "in any order" - remove that; the header must match the agreed column list exactly, in order.
