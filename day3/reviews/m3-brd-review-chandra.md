# HITL_REVIEW - m3 BRD

**Artefact:** `day3/outputs/m3-brd-chandra-v1.md` (draft `v1`)
**Reviewer:** `chandra`   **Date:** `2026-11-26`

_Drafted locally (Bedrock sandbox creds expired at review time). Checklist and scores below are the AI's
self-assessment — provisional until Chandra confirms. Regenerate v1 with `draft_brd.py` when creds return._

## BRD checks (tick each; every unticked item becomes a revision note)

- [x] Every functional requirement has a testable acceptance criterion (a number, a file, a route)
- [x] Record-level routing is stated: validated / quarantine by reason / flagged / skipped duplicate (FR-3)
- [x] Whole-file rejection (header drift) is a requirement, not an afterthought (FR-1)
- [x] The four repeats have requirements: duplicate delivery, same-content resend, duplicate ticket row,
      deliberate rerun after a rule fix (FR-4, FR-5, FR-6)
- [x] Reruns are reproducible: dates relative to the file; source, rules, reference data and code versions
      recorded (FR-6 pins, FR-8 manifest)
- [x] Audit: raw is kept unchanged; every output can be traced to its source file and line
      (§5 raw + sidecar `source_line`)
- [x] Observability NFR names the logging contract (docs/LOGGING_CONTRACT.md): JSON, correlation_id, no payloads
- [x] No implementation choices (no service names, no code) - that's Day 4's design
- [x] Unknowns are open questions, not invented requirements (§6, seven questions)
- [x] Airline vocabulary (pnr, ticket_number, fare_class, od_pair), no order_id / customer

## Score (0-3 per dim · PASS = ≥12/15 AND every dim ≥2)

| Dim | Score | Evidence (one line) |
|---|---|---|
| Correctness (complete, testable, true to the BR) | 3 | FR-1..FR-9 each carry a numeric/file/route acceptance test |
| Security (PII handling, access to quarantine, no data in logs) | 2 | NFR + §5 name no-payload logging and quarantine readers; no explicit threat/access model |
| Observability (logging contract + manifest as requirements) | 3 | NFR names the contract; FR-8 makes the manifest a requirement with balance check |
| Scalability (volume, peaks, reruns, late and duplicate files) | 2 | Volume and rerun isolation stated; no per-file latency or per-record throughput target |
| Cost (retention per zone, no over-specification) | 2 | Per-zone retention tabled + measurable per-record cost; no figure targets by design |
| **TOTAL** | 12/15 | provisional — human to confirm |

**Verdict:** PASS (provisional — awaiting human sign-off)

## Revision notes for v2 (paste these into the prompt's REVISION section)
1. Add a latency/throughput target for a 100k-record file inside the settlement window (Scalability).
2. State who is authorised to create corrections and how their identity is verified (Security/HITL).
3. State whether quarantine feedback goes back to the agency, or answer it in open questions (§6 Q7).
