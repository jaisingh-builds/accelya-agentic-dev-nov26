# HITL_REVIEW - m1-arch-options-chandra

---

**Artefact:** `day1/outputs/m1-arch-options-chandra.md` - Option 1 (the only option returned)
**Reviewer:** `chandra`
**Date:** 2026-11-24
**Module:** `M1`
**Lab:** `lab-1 (architecture options)`

---

## The score (0-3 per dim · PASS = ≥12/15 AND every dim ≥2)

| Dim            | Score | Evidence (one line per dim)                          |
|----------------|-------|------------------------------------------------------|
| Correctness    | 1     | One option of the two required, cut off mid-risk; QuickSight cannot read DynamoDB and its refresh misses the 5-minute target |
| Security       | 1     | IAM lists actions with no ARN-scoped resources or Conditions; two hop principals are wrong; no Object Lock for "immutable" |
| Observability  | 1     | No PNR/fare logging (good), but no `error_type`, no metric, and correlation ID dropped after Lambda |
| Scalability    | 1     | Duplicate S3 events are idempotent, but re-uploads get a new `version-id` and double-count; wrong risks named |
| Cost           | 1     | Lambda + on-demand DynamoDB fit, but QuickSight is heavy and paid for one counter; cost never discussed |
| **TOTAL**      | 5/15  |                                                      |

**Logging contract (from Day 2, mandatory):** design artefact, no logs yet. The design does not break the
contract by intent (identifiers and counts only), but it names no classified errors and drops the
correlation ID downstream, so Observability stays at 1.

**Verdict:** REVISE

---

## Per-dimension detail

### Correctness - `1/3`

- The answer hit the 1200 output-token limit (`tokens out=1200`): Option 2, the comparison and the end of
  Risk 2 are missing. "Return exactly TWO options" is not met.
- Hop 5: QuickSight has no direct DynamoDB data source (needs an Athena DynamoDB connector or an S3 export),
  and SPICE refreshes at best every 15 minutes, not every 2. The "within 5 minutes" must fails.
- The "must not double-count re-uploads" requirement fails (see Scalability).
- No edge cases named: empty file, header only, unknown `airline_code`, mixed airlines in one file.
- The prompt still contains `columns: ...`, so the model guessed the CSV schema.

### Security - `1/3`

- No literal `*`, but no resource ARNs are given either; `dynamodb:Scan` for QuickSight is broader than needed.
- Hop 1: S3 -> EventBridge needs EventBridge notifications enabled on the bucket, not a bucket policy for
  `events.amazonaws.com`.
- Hop 2: EventBridge invokes Lambda through a Lambda resource-based permission (`lambda:InvokeFunction` for
  `events.amazonaws.com` with `aws:SourceArn`), not an `EventBridgeInvokeLambdaRole`.
- Role names do not follow `accelya-m1-chandra-`.
- "Immutable" needs S3 Object Lock or a bucket policy denying `s3:DeleteObject*`; versioning alone is not enough.

### Observability - `1/3`

- Credit: "no PNR/fare logged" and "logged per row count only" respect the logging contract.
- Not 2: no classified errors (`EmptyFile`, `HeaderMismatch`, `UnknownAirline`) for the failure path, and no
  metric finance can alarm on.
- Hop 5 says the correlation ID is "not needed downstream"; it must be on every application log line, sourced
  from `x-amz-meta-correlation-id` or minted by the Lambda.

### Scalability - `1/3`

- Idempotency is partial: the conditional write on `key + version-id` absorbs a duplicate S3 event, but every
  re-upload creates a new `version-id`, so the re-sent file is counted again.
- Memory: "reads up to 20 MB in memory" - bounded, but not streamed.
- Wrong risks named: a 15-minute timeout is not real for a 20 MB file, and 50-200 files a day cannot create a
  DynamoDB hot partition. The real risk (double-counting re-uploads) is missing.

### Cost - `1/3`

- Lambda + DynamoDB on demand is the workload-appropriate core.
- QuickSight (subscription + Athena connector) is over-sized for one count per airline.
- Cost is never discussed; the comparison where it would be weighed was cut off.

---

## If REVISE: what to change

1. **Correctness** - Re-run with the updated script (2500 output tokens, truncation warning) and fill in the
   CSV columns in `day1/prompts/m1-arch-options.md` so both options and the comparison arrive complete.
2. **Scalability / Correctness** - Key the DynamoDB item on `file_key + checksum` (eTag or SHA-256), not
   `version-id`, with `ConditionExpression: attribute_not_exists(pk)`. An identical re-send is a no-op; a
   re-send with new content replaces that file's count (never adds) and is marked `status = RESENT`.
3. **Correctness / Cost** - Replace QuickSight with an EMF metric `TicketCount` (dimension `airline_code`)
   emitted by the Lambda and shown on a CloudWatch dashboard: visible within seconds, near-zero cost.
4. **Security** - Enable EventBridge notifications on the bucket; Lambda permission for
   `events.amazonaws.com` with `aws:SourceArn`; Lambda role `s3:GetObject` on
   `arn:aws:s3:::accelya-m1-chandra-<acct>/raw/*` only and `dynamodb:PutItem`/`UpdateItem` on the table ARN;
   add Object Lock or deny deletes for immutability; prefix every role `accelya-m1-chandra-`.
5. **Observability** - Every log line carries `correlation_id`, `file_key`, `airline_code`, `row_count`,
   `status`; ERROR lines add `error_type`.
6. **Failure path** - SQS DLQ on the EventBridge target (delivery failures) plus a Lambda on-failure
   destination (processing failures).

**Next iteration prompt addition:**
> Make every option idempotent for duplicate S3 events AND re-uploaded files: state the exact DynamoDB key and
> condition expression; a re-upload with new content replaces, never adds. Do not use QuickSight; show counts
> via a CloudWatch EMF metric per airline_code. Give IAM as principal + named actions + ARN-scoped resource,
> with role names starting `accelya-m1-chandra-`. Name real edge cases (empty file, header-only, unknown
> airline_code) with an `error_type` each. Never log row contents.

---

## Expected re-score after the revision

| Dim | Before | After | Why |
|---|---|---|---|
| Correctness | 1 | 3 | Both options complete; re-upload rule designed; edge cases named |
| Security | 1 | 2 | Correct principals, ARN-scoped, no PII in logs (3 needs Conditions everywhere + PII tagging) |
| Observability | 1 | 2 | Contract-conformant logs + classified errors (3 needs EMF in code) |
| Scalability | 1 | 2 | Idempotent for events and re-uploads + streamed parse (3 needs a 10x test) |
| Cost | 1 | 2 | Workload-appropriate services, cost weighed in the comparison |
| **Total** | **5** | **11-12** | **PASS only if all of the above land** |

**Teaching point:** the model wrote a confident, well-formatted design whose dedup key silently fails the one
"must" that protects the money (re-uploads), and whose dashboard cannot meet the 5-minute target. Both look
plausible on a first read - that is why a human scores it.

---

*Pass bar: ≥12/15 AND every dim ≥2.*
