# Day 1 shell setup. Run from the repo root in EVERY new terminal:
#     source day1/scripts/env.sh <your-id>
# Sets: AWS_PROFILE (accelya, if you created it), AWS_DEFAULT_REGION (us-east-1),
#       L (learner id), F (sam deploy flags), B (your bucket), LG (your Lambda log group),
#       BEDROCK_MODEL_ID (default model for Lab 1)
if [ -z "$1" ]; then echo "usage: source day1/scripts/env.sh <your-id>"; return 1 2>/dev/null || exit 1; fi
if aws configure list-profiles 2>/dev/null | grep -qx accelya; then export AWS_PROFILE=accelya; fi
export L="$1"
export AWS_DEFAULT_REGION="${ACCELYA_REGION:-us-east-1}"     # Pluralsight sandbox: us-east-1 or us-west-2 only
# --resolve-s3: SAM creates/uses its own artifact bucket for the code package
# CAPABILITY_NAMED_IAM: required because the template creates IAM roles with fixed names
export F="--resolve-s3 --capabilities CAPABILITY_NAMED_IAM --region $AWS_DEFAULT_REGION"
export BEDROCK_MODEL_ID="${BEDROCK_MODEL_ID:-us.anthropic.claude-3-5-sonnet-20241022-v2:0}"
ACCT=$(aws sts get-caller-identity --query Account --output text 2>/dev/null)
if [ -z "$ACCT" ]; then echo "AWS credentials not working - run: aws configure --profile accelya"; return 1 2>/dev/null || exit 1; fi
export B="accelya-m1-${L}-${ACCT}"
export LG="/aws/lambda/accelya-m1-${L}-ingest"
echo "profile=${AWS_PROFILE:-default}  region=$AWS_DEFAULT_REGION  L=$L"
echo "B=$B"
echo "LG=$LG"
