#!/usr/bin/env bash
# Delete the csv-count stack. The landing bucket is versioned, so every object version and
# delete marker is purged first (aws s3 rm --recursive is not enough).
# Usage: bash day1/src/csv-count/cleanup.sh <learner-id>
set -euo pipefail
L=${1:?usage: cleanup.sh <learner-id>}
export AWS_DEFAULT_REGION=${AWS_DEFAULT_REGION:-us-east-1}
[ -n "${AWS_PROFILE:-}" ] || { aws configure list-profiles 2>/dev/null | grep -qx accelya && export AWS_PROFILE=accelya; }
ACCT=$(aws sts get-caller-identity --query Account --output text)
BUCKET="accelya-m1-${L}-csv-count-${ACCT}"
STACK="accelya-m1-${L}-csv-count"
PY=$(command -v python3 || command -v python)

if aws s3api head-bucket --bucket "$BUCKET" 2>/dev/null; then
  echo "Purging all object versions in s3://${BUCKET} ..."
  "$PY" -c 'import sys, boto3; boto3.resource("s3").Bucket(sys.argv[1]).object_versions.delete()' "$BUCKET"
fi

if aws cloudformation describe-stacks --stack-name "$STACK" >/dev/null 2>&1; then
  echo "Deleting stack $STACK ..."
  sam delete --stack-name "$STACK" --no-prompts --region "$AWS_DEFAULT_REGION"
fi

echo "Left-overs check (should print nothing):"
aws s3 ls | grep "$BUCKET" || true
echo "Done."
