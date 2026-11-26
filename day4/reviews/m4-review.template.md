# M4 peer review - <reviewer-id> reviews <author-id>

Use `docs/HITL_REVIEW_TEMPLATE.md` for the 5-dimension score. Paste the evidence below.

## Evidence (paste the output, don't summarise it)

| Check | Command | Result |
|---|---|---|
| Tests | `python3 day4/tests/run_tests.py` | __/23, structure OK / NOT OK |
| Logging contract (deployed) | `aws logs tail $LG --since 30m --format short \| python3 day2/scripts/check_logs.py -` | CONFORMANT / __ violations |
| correlation_id on every line | `aws logs tail $LG --since 30m --format short \| grep -c '"correlation_id"'` vs total lines | __ of __ |
| Retention | `aws logs describe-log-groups --log-group-name-prefix $LG --query 'logGroups[0].retentionInDays'` | __ days |
| Manifest | `aws s3 cp s3://$B/manifests/business_date=2026-11-25/ticket_issue_AI_2026-11-25.csv.manifest.json -` | balanced: __ |

## Read the code (one line each)

1. Where does a **transient** S3 error end up? Where does a **bad record** end up? Are they different paths?
2. Find one log call. Could any value in it be a payload field? Which key, which value?
3. What makes a second run of the same file a no-op? Which line of code?
4. One thing you'd change before this goes to production, and why.

## Verdict
PASS / REVISE - and the single most important fix.
