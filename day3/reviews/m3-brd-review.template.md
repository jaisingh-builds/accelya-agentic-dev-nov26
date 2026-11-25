# HITL_REVIEW - m3 BRD

**Artefact:** `day3/outputs/m3-brd-<you>.md` (draft `v1` / `v2`)
**Reviewer:** `<your-id>`   **Date:** `YYYY-MM-DD`

## BRD checks (tick each; every unticked item becomes a revision note)

- [ ] Every functional requirement has a testable acceptance criterion (a number, a file, a route)
- [ ] Record-level routing is stated: validated / quarantine by reason / flagged / skipped duplicate
- [ ] Whole-file rejection (header drift) is a requirement, not an afterthought
- [ ] The four repeats have requirements: duplicate delivery, same-content resend, duplicate ticket row, deliberate rerun after a rule fix
- [ ] Reruns are reproducible: dates relative to the file; source, rules, reference data and code versions recorded
- [ ] Audit: raw is kept unchanged; every output can be traced to its source file and line
- [ ] Observability NFR names the logging contract (docs/LOGGING_CONTRACT.md): JSON, correlation_id, no payloads
- [ ] No implementation choices (no service names, no code) - that's Day 4's design
- [ ] Unknowns are open questions, not invented requirements
- [ ] Airline vocabulary (pnr, ticket_number, fare_class, od_pair), no order_id / customer

## Score (0-3 per dim · PASS = ≥12/15 AND every dim ≥2)

| Dim | Score | Evidence (one line) |
|---|---|---|
| Correctness (complete, testable, true to the BR) | | |
| Security (PII handling, access to quarantine, no data in logs) | | |
| Observability (logging contract + manifest as requirements) | | |
| Scalability (volume, peaks, reruns, late and duplicate files) | | |
| Cost (retention per zone, no over-specification) | | |
| **TOTAL** | /15 | |

**Verdict:** PASS / REVISE

## Revision notes for v2 (paste these into the prompt's REVISION section)
1.
2.
3.
