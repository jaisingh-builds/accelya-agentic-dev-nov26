# Day 1 shell setup. Run from the repo root:   source day1/scripts/env.sh <your-id>
# Sets: L (learner id), F (sam deploy flags), B (your bucket), LG (your Lambda log group)
if [ -z "$1" ]; then echo "usage: source day1/scripts/env.sh <your-id>"; return 1 2>/dev/null || exit 1; fi
export L="$1"
export AWS_DEFAULT_REGION=ap-south-1
export F="--resolve-s3 --capabilities CAPABILITY_NAMED_IAM --region ap-south-1"
ACCT=$(aws sts get-caller-identity --query Account --output text 2>/dev/null)
if [ -z "$ACCT" ]; then echo "AWS credentials not working - run aws configure first"; return 1 2>/dev/null || exit 1; fi
export B="accelya-m1-${L}-${ACCT}"
export LG="/aws/lambda/accelya-m1-${L}-ingest"
echo "L=$L   B=$B"
echo "LG=$LG"
