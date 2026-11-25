#!/usr/bin/env bash
# End-of-day cleanup. Usage: bash day1/scripts/cleanup_day1.sh <learner-id>
set -euo pipefail
L=${1:?usage: cleanup_day1.sh <learner-id>}
export AWS_DEFAULT_REGION=${AWS_DEFAULT_REGION:-us-east-1}
[ -n "${AWS_PROFILE:-}" ] || { aws configure list-profiles 2>/dev/null | grep -qx accelya && export AWS_PROFILE=accelya; }
ACCT=$(aws sts get-caller-identity --query Account --output text)
BUCKET="accelya-m1-${L}-${ACCT}"

for B in "$BUCKET" "accelya-m1-${L}-landing-${ACCT}"; do
  echo "Emptying s3://${B} ..."
  aws s3 rm "s3://${B}" --recursive || true
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
