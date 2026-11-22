#!/usr/bin/env bash
# End-of-day cleanup. Usage: bash day1/scripts/cleanup_day1.sh <learner-id>
set -euo pipefail
L=${1:?usage: cleanup_day1.sh <learner-id>}
export AWS_DEFAULT_REGION=ap-south-1
ACCT=$(aws sts get-caller-identity --query Account --output text)
BUCKET="accelya-m1-${L}-${ACCT}"

echo "Emptying s3://${BUCKET} ..."
aws s3 rm "s3://${BUCKET}" --recursive || true

for STACK in "accelya-m1-${L}" "accelya-m1-${L}-dup"; do
  if aws cloudformation describe-stacks --stack-name "$STACK" >/dev/null 2>&1; then
    echo "Deleting stack $STACK ..."
    sam delete --stack-name "$STACK" --no-prompts --region ap-south-1
  fi
done

echo "Left-overs check (should print nothing):"
aws lambda list-functions --query "Functions[?starts_with(FunctionName,'accelya-m1-${L}')].FunctionName" --output text
aws s3 ls | grep "accelya-m1-${L}" || true
echo "Done."
