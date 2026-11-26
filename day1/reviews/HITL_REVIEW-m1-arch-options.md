# HITL_REVIEW — m1-arch-options (Option 1)

> **Template.** Copy to `reviews/HITL_REVIEW-<artefact-name>.md` in your branch for each artefact.

---

**Artefact:** `day1/outputs/run3/m1-arch-options-shrivatsa.md` - Option 1 only (S3 -> Lambda -> DynamoDB -> API Gateway -> CloudFront)
**Reviewer:** `<your-github-handle>`
**Date:** 2026-11-24
**Module:** `M1`
**Lab:** `lab-1`

---

## The score (0–3 per dim · PASS = ≥12/15 AND every dim ≥2)

| Dim            | Score | Evidence (one line per dim)                          |
|----------------|-------|------------------------------------------------------|
| Correctness    | 3     | Covers all 3 Musts: original file stays in S3 (Hop 1), failed-files bucket (Hop 3), SNS alert that does not block processing (Hop 4), count served to a dashboard (Hops 2, 5, 6). |
| Security       |       |                                                      |
| Observability  |       |                                                      |
| Scalability    |       |                                                      |
| Cost           |       |                                                      |
| **TOTAL**      | __/15 |                                                      |

**Logging contract (from Day 2, mandatory):** `python3 day2/scripts/check_logs.py <log>` → CONFORMANT / __ violations
*A non-conformant artefact scores at most 1 on Observability.*

**Verdict:** PASS / REVISE

---

## Per-dimension detail

### Correctness — `3/3`

**Anchors:**
- **3** — matches spec + edge cases tested
- **2** — matches spec; happy path only
- **1** — matches spec but missing field or wrong API name
- **0** — doesn't compile / hallucinated APIs

**Evidence:** Covers all 3 Musts: original file stays in S3 (Hop 1), failed-files bucket (Hop 3), SNS alert that does not block processing (Hop 4), count served to a dashboard (Hops 2, 5, 6).

---

### Security — `<score>/3`

**Anchors:**
- **3** — ARN-scoped IAM + named verbs + Conditions; secrets via env; PII tagged
- **2** — no wildcards; secrets via env; PII not addressed
- **1** — some wildcards present
- **0** — `Action: "*"` / `Resource: "*"` / hardcoded secrets

**Evidence:** `<bullet>`

---

### Observability — `<score>/3`

**Anchors:**
- **3** — powertools Logger + correlation_id + EMF + classified errors
- **2** — JSON logs + classified errors; no metrics yet
- **1** — JSON logs only
- **0** — `print` statements / silent `except: pass`

**Evidence:** `<bullet>`

---

### Scalability — `<score>/3`

**Anchors:**
- **3** — idempotent + bounded memory + maintainable + tested at 10× scale
- **2** — idempotent + bounded; maintainability OK
- **1** — idempotent only
- **0** — unbounded memory, non-idempotent, OR over-abstracted

**Evidence:** `<bullet>`

---

### Cost — `<score>/3`

**Anchors:**
- **3** — workload-appropriate service + sampled where applicable + cost-per-record measurable
- **2** — workload-appropriate service; no sampling discipline
- **1** — workload-appropriate; cost not considered
- **0** — wrong service for the workload

**Evidence:** `<bullet>`

---

## If REVISE: what to change

1. **Correctness / Scalability** — DynamoDB: make `agency_iata` part of the sort key (e.g. pk `AIRLINE#<airline_code>`, sk `DATE#<date>#AGENCY#<agency_iata>`), so each agency's file is its own item. A re-upload overwrites its own item (no double count), and the dashboard sums an airline's items for the day with one Query. State this in Hop 2.
2. **Security** — No public API Gateway. Hop 6 ("public API with WAF rules") must be replaced by a private, authenticated way for finance to read the counts, with the IAM role/auth named in the hop.

**Next iteration prompt addition:** `DynamoDB sort key must include agency_iata so a re-upload overwrites its own item; the dashboard sums per airline and date. The finance dashboard must not use a public endpoint; access must be authenticated.`

---

*Pass bar: ≥12/15 AND every dim ≥2.*
