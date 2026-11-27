#!/usr/bin/env bash
# End-of-day cleanup. Usage: bash day4/scripts/cleanup_day4.sh <learner-id>
set -euo pipefail
L=${1:?usage: cleanup_day4.sh <learner-id>}
export AWS_DEFAULT_REGION=${AWS_DEFAULT_REGION:-us-east-1}
ACCT=$(aws sts get-caller-identity --query Account --output text)
BUCKET="accelya-m4-${L}-${ACCT}"
STACK="accelya-m4-${L}"
if aws s3api head-bucket --bucket "$BUCKET" >/dev/null 2>&1; then
  echo "Emptying s3://${BUCKET} ..."
  aws s3 rm "s3://${BUCKET}" --recursive --quiet
fi
if aws cloudformation describe-stacks --stack-name "$STACK" >/dev/null 2>&1; then
  echo "Deleting stack $STACK ..."
  sam delete --stack-name "$STACK" --no-prompts --region "$AWS_DEFAULT_REGION"
fi
echo "Left-overs check (should print nothing):"
aws lambda list-functions --query "Functions[?starts_with(FunctionName,'accelya-m4-${L}')].FunctionName" --output text
aws s3 ls | grep "accelya-m4-${L}" || true
aws logs describe-log-groups --log-group-name-prefix "/aws/lambda/accelya-m4-${L}" --query 'logGroups[].logGroupName' --output text
echo "Done."
