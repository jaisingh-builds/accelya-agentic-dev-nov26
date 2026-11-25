# Day 1 shell setup. Run from the repo root in EVERY new terminal:
#     source day1/scripts/env.sh <your-id>
# Sets: AWS_PROFILE (accelya, if you created it), AWS_DEFAULT_REGION (us-east-1),
#       L (learner id), F (sam deploy flags), B (your bucket), LG (your Lambda log group),
#       (no BEDROCK_MODEL_ID default: the scripts choose a current model)
if [ -z "$1" ]; then echo "usage: source day1/scripts/env.sh <your-id>"; return 1 2>/dev/null || exit 1; fi
if aws configure list-profiles 2>/dev/null | grep -qx accelya; then export AWS_PROFILE=accelya; fi
export L="$1"
# zsh (macOS default) does not split $F into words like bash does; make it behave the same.
[ -n "$ZSH_VERSION" ] && setopt SH_WORD_SPLIT
export AWS_DEFAULT_REGION="${ACCELYA_REGION:-us-east-1}"     # Pluralsight sandbox: us-east-1 or us-west-2 only
# --resolve-s3: SAM creates/uses its own artifact bucket for the code package
# CAPABILITY_NAMED_IAM: required because the template creates IAM roles with fixed names
export F="--resolve-s3 --capabilities CAPABILITY_NAMED_IAM --region $AWS_DEFAULT_REGION"
export BEDROCK_MODEL_ID="${BEDROCK_MODEL_ID:-us.anthropic.claude-haiku-4-5-20251001-v1:0}"
ACCT=$(aws sts get-caller-identity --query Account --output text 2>/dev/null)
if [ -z "$ACCT" ]; then
  echo "NOTE: standard-sandbox profile 'accelya' not working yet. Fine for Lab 1 (uses accelya-ai)."
  echo "      Before Lab 2: open the standard AWS sandbox, aws configure --profile accelya, then source this again."
  ACCT="<acct>"
fi
export B="accelya-m1-${L}-${ACCT}"
export LG="/aws/lambda/accelya-m1-${L}-ingest"
echo "profile=${AWS_PROFILE:-default}  region=$AWS_DEFAULT_REGION  L=$L"
echo "B=$B"
echo "LG=$LG"
