# Architecture options (2) - output for m1-arch-options-solution.md

_Option A is what was built and deployed (stack `accelya-m1-<id>`, us-east-1). Option B was considered and
not built. Synthetic scenario; no real Accelya data. The three files in `day1/data/` are test fixtures
(12, 18 and 25 data rows), not production-size files._

## Option A - S3 event -> EventBridge -> SQS -> Lambda counter -> DynamoDB  (BUILT)

Summary: an upload to `raw/*.csv` raises an `Object Created` event; EventBridge puts it on an SQS queue;
one Lambda streams the CSV, validates it, counts the data rows and upserts one result item per airline
and date in DynamoDB, which finance reads.

Services in order: agency upload -> S3 `raw/` -> EventBridge rule `raw-csv` -> SQS `count-rows`
(+ DLQ) -> Lambda `count-rows` -> DynamoDB `file-counts`

| Hop | 1 Push/pull | 2 Sync/async | 3 Payload | 4 Role | 5 Failure path | 6 Correlation ID |
|---|---|---|---|---|---|---|
| S3 -> EventBridge | push | async | `Object Created` event (bucket, key, size, version-id) | bucket EventBridge notifications on | EventBridge retries delivery | event `id` |
| EventBridge -> SQS | push | async | same event JSON | `events.amazonaws.com` allowed `sqs:SendMessage`, only from this rule's ARN | 24 retries / 1 h, then rule DLQ | carried in the message body |
| SQS -> Lambda | pull (event source mapping) | async | one event per batch (BatchSize 1) | Lambda role: `sqs:ReceiveMessage`, `DeleteMessage`, `GetQueueAttributes` on this queue | 3 receives, then SQS DLQ (14-day retention) | `m1-<request id>-<event id>` minted per file |
| Lambda -> S3 | pull | sync | streamed object body (never logged) | Lambda role: `s3:GetObject`, `s3:GetObjectVersion` on `raw/*` only | bad file -> FAILED row, no retry; AWS error -> raise, retry, DLQ | on every log line |
| Lambda -> DynamoDB | push | sync PutItem | `{pk, airline_code, file_date, file_name, row_count, status, version_id, updated_at, correlation_id, reason?}` | Lambda role: `dynamodb:PutItem` on `file-counts` | conditional write; transient error -> raise, retry, DLQ | stored on the item |

Implementation notes:
- File name must match `ticket_issue_<AIRLINE>_<YYYY-MM-DD>.csv`; header must be the 9 expected columns.
- Rows are counted with the `csv` module (quoted newlines count once, BOM handled, blank rows skipped).
- `pk = <airline>#<file date>`. A re-sent file overwrites its item (never adds). The write is conditional
  (`attribute_not_exists(pk) OR updated_at <= :t`), so an older event cannot overwrite a newer one.
- Unusable file (name, header, encoding, CSV syntax) -> `status = FAILED`, `row_count = 0`, `reason`
  set, no exception, so finance still sees a row.
- Logs are one JSON object per line with `correlation_id`; they hold file name, airline, count and status
  only. No fare_amount, PNRs, ticket numbers or rows are ever logged.

Bucket (`accelya-m1-<id>-<acct>`): versioned, AES256, public access blocked, TLS-only policy, lifecycle
on `raw/` (non-current versions expire after 30 days, incomplete multipart uploads after 7 days).
Log group `/aws/lambda/accelya-m1-<id>-count-rows`, 7-day retention. Table: on-demand, point-in-time
recovery on.

Risks: (1) the airline team's bucket must have EventBridge notifications on and 7-year retention
(Object Lock or lifecycle); without them nothing fires or the audit copy is not guaranteed.
(2) A file larger than expected could approach the 30 s function timeout; raise the timeout and the queue
visibility timeout (keep it at least 6x the timeout) if production files take longer.

Cost driver: Lambda duration per file (about 40 files a day); SQS and DynamoDB on-demand are negligible.

## Option B - S3 -> EventBridge -> Step Functions -> Lambda x2 -> DynamoDB  (NOT BUILT)

Summary: each file starts a state machine with explicit validate and count steps; a Catch state writes
the FAILED row.

Services in order: S3 `raw/` -> EventBridge rule -> Step Functions -> Lambda `validate` -> Lambda
`count` -> DynamoDB `file-counts` (SDK integration)

| Hop | 1 Push/pull | 2 Sync/async | 3 Payload | 4 Role | 5 Failure path | 6 Correlation ID |
|---|---|---|---|---|---|---|
| S3 -> EventBridge | push | async | `Object Created` event | bucket EventBridge notifications on | S3 event retries | execution name = `correlation_id` |
| EventBridge -> Step Functions | push | async StartExecution | event detail | rule role: `states:StartExecution` | rule DLQ | in state input |
| Step Functions -> Lambda `validate` | push | sync | key, versionId (no contents) | reader role, `s3:GetObject` on `raw/*` | Catch -> record FAILED | in state input |
| Step Functions -> Lambda `count` | push | sync | key, versionId | same reader role | retry with backoff, then Catch -> record FAILED | in state input |
| Step Functions -> DynamoDB | push | sync SDK integration | result item | execution role: `PutItem` only | retry, then execution FAILED + alarm | on the item |

Risks: (1) same EventBridge dependency on the airline bucket. (2) Standard workflows bill per state
transition and keep execution history; a re-send burst adds cost and noise.

Cost driver: state transitions (about 4 per file), then Lambda duration.

## Comparison

| | A: SQS + Lambda (built) | B: Step Functions |
|---|---|---|
| Meets 5 minutes | yes, seconds | yes, seconds |
| Per-airline counts | yes | yes |
| Re-sent files | yes (upsert by airline#date, conditional write) | yes (same) |
| Failed files visible | yes (FAILED row, 0 rows) | yes (Catch state) |
| Lambda functions used (limit 10) | 1 | 2 |
| Moving parts | 5 | 6 |
| Relative cost | lowest | low |

Recommendation: Option A. Choose B only if finance or audit needs a per-file execution record showing
each step.

## Where it lives in the repo

| What | Path |
|---|---|
| Stack (bucket, EventBridge rule, queues, table, roles, functions) | `day1/template.yaml` |
| Lambda handler | `day1/src/count_rows/app.py` |
| Local fixture test (no AWS needed) | `day1/tests/test_count_rows.py` |
| Sample data | `day1/data/ticket_issue_{AI,BA,EK}_2026-11-23.csv` |

## Redeploy in a new sandbox

Run from Git Bash at the repo root, after `aws configure --profile <profile>` for the new sandbox
(never paste keys into chat or commit them):

```bash
alias sam=sam.cmd
export AWS_PROFILE=<profile> AWS_DEFAULT_REGION=us-east-1
L=<lowercase-id>          # 2-20 chars: lowercase letters, digits, hyphen
python day1/tests/test_count_rows.py          # expect ALL PASS
sam build -t day1/template.yaml
sam deploy -t .aws-sam/build/template.yaml --stack-name accelya-m1-$L \
  --parameter-overrides LearnerId=$L \
  --resolve-s3 --capabilities CAPABILITY_NAMED_IAM --region us-east-1
```

Smoke test (expect `AI#2026-11-23`, 25 rows, `OK`):

```bash
B=accelya-m1-$L-$(aws sts get-caller-identity --query Account --output text)
aws s3 cp day1/data/ticket_issue_AI_2026-11-23.csv s3://$B/raw/
sleep 20
aws dynamodb scan --table-name accelya-m1-$L-file-counts \
  --query 'Items[].{pk:pk.S,rows:row_count.N,status:status.S}'
aws logs tail /aws/lambda/accelya-m1-$L-count-rows --since 5m
```

Notes for a new sandbox:
- The template creates IAM roles with fixed names, so the profile needs `iam:CreateRole`. If it does not,
  pass `ExistingRoleArn`; that fallback role must also allow the S3, SQS, DynamoDB and log actions in the
  table above, or the function will fail.
- Bucket and role names include the learner id, so deploying twice in one account needs different ids.
- To remove everything: `aws cloudformation delete-stack --stack-name accelya-m1-$L` (empty the bucket
  first; it is versioned, so delete object versions too).
- Not built yet: a finance-facing view (API Gateway or Athena) over the table, and alarms on the two DLQs.
