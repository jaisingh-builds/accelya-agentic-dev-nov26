#!/usr/bin/env bash
# M3 Lab 1 rerun: deploy the Day-1 stack, exercise the raw/ trigger, archive logs, clean up.
#
# Usage (from the repo root, Git Bash):
#     bash day3/scripts/lab1_redeploy.sh <learner-id> [--keep]
#
#   --keep   leave the stack deployed after the checks (cleanup skipped).
#            Remove it later with: bash day1/scripts/cleanup_day1.sh <learner-id>
#
# Mirrors day3/LABS.md Lab 1. The duplicate step may legitimately exceed the expected
# count: S3 notifications are at-least-once, so an extra delivery can arrive on its own.
# On Windows/Git Bash, log-group names need MSYS_NO_PATHCONV=1 and Lambda invoke needs
# a real outfile path (no /dev/null).

L=${1:?usage: bash day3/scripts/lab1_redeploy.sh <learner-id> [--keep]}
KEEP=${2:-}

export AWS_PROFILE="${AWS_PROFILE:-accelya}"
export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-us-east-1}"
export MSYS_NO_PATHCONV=1

# env.sh derives B and LG from the account your profile resolves to.
source day1/scripts/env.sh "$L"
case "$B" in
  *"<"*) echo "AWS credentials not working (B=$B). Refresh your sandbox profile, then rerun."; exit 1;;
esac
source .venv/Scripts/activate 2>/dev/null || source .venv/bin/activate 2>/dev/null || true

K=raw/ticket_issue_AI_2026-11-25.csv
step() { echo; echo "=== $* ==="; }

step "1. sam build + deploy (stack accelya-m1-$L)"
(cd day1 && sam build && sam deploy --stack-name "accelya-m1-$L" $F \
  --parameter-overrides LearnerId="$L" --no-confirm-changeset --no-fail-on-empty-changeset) || exit 1

step "2. land the file in raw/ (correlation $L-d3-001)"
aws s3 cp day3/data/ticket_issue_AI_2026-11-25.csv "s3://$B/raw/" --metadata "correlation-id=$L-d3-001" || exit 1
ETAG1=$(aws s3api head-object --bucket "$B" --key "$K" --query ETag --output text)
echo "ETag 1: $ETAG1"
sleep 30
N=$(aws logs tail "$LG" --since 15m --format short | grep "$L-d3-001" | grep -c file.received || true)
echo "file.received for $L-d3-001: $N  (expect 1)"

step "3. bucket ROOT upload must not trigger (correlation $L-d3-002)"
aws s3api get-bucket-notification-configuration --bucket "$B" --query 'LambdaFunctionConfigurations[].Filter'
aws s3 cp day3/data/ticket_issue_AI_2026-11-25.csv "s3://$B/" --metadata "correlation-id=$L-d3-002" || exit 1
sleep 60
N=$(aws logs tail "$LG" --since 15m --format short | grep -c "$L-d3-002" || true)
echo "log lines for $L-d3-002: $N  (expect 0)"

step "4. replay the S3 event once (simulated duplicate delivery)"
python3 day3/scripts/s3_event.py --bucket "$B" --key "$K" > day3/outputs/s3_event.json || exit 1
aws lambda invoke --function-name "accelya-m1-$L-ingest" --invocation-type Event \
  --cli-binary-format raw-in-base64-out --payload file://day3/outputs/s3_event.json \
  day3/outputs/invoke-out.json || exit 1
sleep 30
N=$(aws logs tail "$LG" --since 15m --format short | grep "$L-d3-001" | grep -c file.received || true)
echo "file.received for $L-d3-001: $N  (expect 2; >2 means S3 redelivered on its own)"

step "5. resend the same bytes (correlation $L-d3-003)"
aws s3 cp day3/data/ticket_issue_AI_2026-11-25.csv "s3://$B/raw/" --metadata "correlation-id=$L-d3-003" || exit 1
ETAG2=$(aws s3api head-object --bucket "$B" --key "$K" --query ETag --output text)
echo "ETag 2: $ETAG2  (equal to ETag 1 = identical bytes)"
sleep 30
N=$(aws logs tail "$LG" --since 15m --format short | grep "$L-d3-003" | grep -c file.received || true)
echo "file.received for $L-d3-003: $N  (expect 1)"

if [ "$KEEP" = "--keep" ]; then
  echo
  echo "Keeping the stack up. Clean up later with: bash day1/scripts/cleanup_day1.sh $L"
else
  step "6. cleanup (archives logs to day1/outputs/logs/ before deleting)"
  bash day1/scripts/cleanup_day1.sh "$L"
fi
