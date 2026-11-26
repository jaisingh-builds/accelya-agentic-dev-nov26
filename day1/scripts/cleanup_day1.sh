#!/usr/bin/env bash
# End-of-day cleanup. Usage: bash day1/scripts/cleanup_day1.sh <learner-id>
set -euo pipefail
L=${1:?usage: cleanup_day1.sh <learner-id>}
export AWS_DEFAULT_REGION=${AWS_DEFAULT_REGION:-us-east-1}
[ -n "${AWS_PROFILE:-}" ] || { aws configure list-profiles 2>/dev/null | grep -qx accelya && export AWS_PROFILE=accelya; }
ACCT=$(aws sts get-caller-identity --query Account --output text)
BUCKET="accelya-m1-${L}-${ACCT}"

echo "Emptying s3://${BUCKET} ..."
aws s3 rm "s3://${BUCKET}" --recursive || true

# Archive application logs locally BEFORE deleting the stack: the delete takes the
# log group with it, and CloudWatch log deletion is unrecoverable.
# MSYS_NO_PATHCONV keeps Git Bash from rewriting the leading / in log group names.
LOG_DIR="day1/outputs/logs"
mkdir -p "$LOG_DIR"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
export MSYS_NO_PATHCONV=1
aws logs describe-log-groups --log-group-name-prefix "/aws/lambda/accelya-m1-${L}" \
    --query 'logGroups[].logGroupName' --output text 2>/dev/null | tr '\t' '\n' | while read -r G; do
  [ -z "$G" ] && continue
  OUT="$LOG_DIR/$(echo "${G#/}" | tr '/ ' '__')-${STAMP}.log"
  aws logs tail "$G" --since 30d --format short > "$OUT" 2>/dev/null || true
  echo "Saved $(grep -c . "$OUT" 2>/dev/null || echo 0) log lines -> $OUT"
done

for STACK in "accelya-m1-${L}" "accelya-m1-${L}-dup"; do
  if aws cloudformation describe-stacks --stack-name "$STACK" >/dev/null 2>&1; then
    echo "Deleting stack $STACK ..."
    sam delete --stack-name "$STACK" --no-prompts --region "$AWS_DEFAULT_REGION"
  fi
done

echo "Left-overs check (should print nothing):"
aws lambda list-functions --query "Functions[?starts_with(FunctionName,'accelya-m1-${L}')].FunctionName" --output text
aws s3 ls | grep "accelya-m1-${L}" || true
echo "Done."
