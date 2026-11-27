#!/usr/bin/env bash
# End-of-day cleanup. Usage: bash day5/scripts/cleanup_day5.sh <learner-id>
# Stops any running task, empties the bucket, deletes the stack (ECR images, Glue database + tables,
# Athena workgroup, cluster, VPC go with it), then prints anything left behind.
set -euo pipefail
L=${1:?usage: cleanup_day5.sh <learner-id>}
export AWS_DEFAULT_REGION=${AWS_DEFAULT_REGION:-us-east-1}
ACCT=$(aws sts get-caller-identity --query Account --output text)
BUCKET="accelya-m5-${L}-${ACCT}"
STACK="accelya-m5-${L}"
CLUSTER="accelya-m5-${L}"
for t in $(aws ecs list-tasks --cluster "$CLUSTER" --query 'taskArns[]' --output text 2>/dev/null || true); do
  echo "Stopping task $t ..."; aws ecs stop-task --cluster "$CLUSTER" --task "$t" >/dev/null
done
if aws s3api head-bucket --bucket "$BUCKET" >/dev/null 2>&1; then
  echo "Emptying s3://${BUCKET} ..."
  aws s3 rm "s3://${BUCKET}" --recursive --quiet
fi
if aws cloudformation describe-stacks --stack-name "$STACK" >/dev/null 2>&1; then
  echo "Deleting stack $STACK (about 2 minutes) ..."
  sam delete --stack-name "$STACK" --no-prompts --region "$AWS_DEFAULT_REGION"
fi
echo "Left-overs check (should print nothing):"
T="--cli-read-timeout 15 --cli-connect-timeout 10"     # a stalled call fails fast instead of hanging the check
aws s3 ls $T | grep "accelya-m5-${L}-" || true
aws ecr describe-repositories $T --query "repositories[?starts_with(repositoryName,'accelya-m5-${L}-')].repositoryName" --output text || true
aws glue get-database $T --name "accelya_m5_$(echo "$L" | tr '-' '_')" --query Database.Name --output text 2>/dev/null || true
aws logs describe-log-groups $T --log-group-name-prefix "/ecs/accelya-m5-${L}-" --query 'logGroups[].logGroupName' --output text || true
echo "Done."
