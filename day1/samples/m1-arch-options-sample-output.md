# Architecture options (2) - SAMPLE OUTPUT for m1-arch-options-solution.md

_Pre-written sample, NOT a live model answer. It is what a typical first answer looks like, flaws
included, so the HITL review in `HITL_REVIEW-m1-arch-options-sample.md` has something real to find.
Synthetic scenario; no real Accelya data._

## Option A - S3 event -> Lambda counter -> DynamoDB

Summary: the upload to `raw/` triggers one Lambda that streams the CSV, counts rows per airline and
writes one summary item per file to DynamoDB, which finance reads through a saved query.

Services in order: agency upload -> S3 `raw/` -> S3 event notification -> Lambda `count-tickets`
-> DynamoDB `file_counts`

| Hop | 1 Push/pull | 2 Sync/async | 3 Payload | 4 Role | 5 Failure path | 6 Correlation ID |
|---|---|---|---|---|---|---|
| S3 -> Lambda | push | async | S3 event JSON (bucket, key, size, eTag) | S3 may invoke via resource policy; Lambda role: `s3:GetObject` on `arn:aws:s3:::<bucket>/*` | Lambda retries async invokes 2 more times, then the event is dropped | minted by the Lambda per file |
| Lambda -> DynamoDB | push | sync PutItem | `{file_key, airline_code, row_count, status, processed_at}` | `dynamodb:PutItem` on `file_counts` | SDK retries; exception fails the invoke | written as an attribute on the item |

Implementation notes: read the object as a stream, count rows per `airline_code`; on a header or
parse error write `row_count = 0, status = FAILED`. For debugging, log the first 5 rows of each file.

Risks: (1) a very large file could exceed Lambda's 15-minute limit. (2) A file that fails 3 times
is lost silently.

Cost driver: Lambda GB-seconds per file; DynamoDB on-demand writes (about 40 per day).

## Option B - S3 -> EventBridge -> Step Functions -> Glue + Lambda -> DynamoDB + SNS

Summary: EventBridge routes `Object Created` events to a Step Functions workflow that runs a Glue job
to count rows, stores the result in DynamoDB and notifies finance on SNS.

Services in order: S3 `raw/` -> EventBridge rule -> Step Functions -> Glue job `count` -> Lambda
`store-result` -> DynamoDB `file_counts` -> SNS `finance-alerts`

| Hop | 1 Push/pull | 2 Sync/async | 3 Payload | 4 Role | 5 Failure path | 6 Correlation ID |
|---|---|---|---|---|---|---|
| S3 -> EventBridge | push | async | `Object Created` event | bucket EventBridge notifications on | EventBridge retries for 24 h | event `id` |
| EventBridge -> Step Functions | push | async StartExecution | event detail | rule role: `states:StartExecution` | rule retries, then rule DLQ | execution name = event `id` |
| Step Functions -> Glue | push | sync (`.sync` integration) | job arguments: bucket, key | `glue:StartJobRun` | Retry/Catch in the state machine | job argument |
| Step Functions -> Lambda -> DynamoDB | push | sync | counts per airline | `lambda:InvokeFunction`; Lambda: `dynamodb:PutItem` | Catch -> FAILED item | execution input |
| Step Functions -> SNS | push | sync Publish | summary text with file name and counts | `sns:Publish` | Retry | in the message |

Risks: (1) Glue job start-up time (often around a minute) eats into the 5-minute target. (2) Many
moving parts for one count.

Cost driver: Glue DPU-minutes per file (minimum billing per run) dominate; Step Functions transitions
are small.

## Comparison

| | A: S3 -> Lambda | B: Step Functions + Glue |
|---|---|---|
| Meets 5 minutes | yes | usually, with Glue start-up risk |
| Per-airline counts | yes | yes |
| Re-sent files | not addressed | not addressed |
| Failed files visible | yes (status FAILED) | yes (Catch) |
| Moving parts | 3 | 7 |
| Relative cost | lowest | highest |

Recommendation: Option A.
