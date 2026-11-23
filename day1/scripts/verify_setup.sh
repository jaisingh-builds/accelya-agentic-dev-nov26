#!/usr/bin/env bash
# Day 1 quick-start check. Usage: bash day1/scripts/verify_setup.sh
# Prints PASS/FAIL per item. Nothing here costs money or uses Bedrock quota.
set -u
pass(){ printf "  \033[32mPASS\033[0m  %s\n" "$1"; }
fail(){ printf "  \033[31mFAIL\033[0m  %s   -> %s\n" "$1" "$2"; FAILS=$((FAILS+1)); }
FAILS=0
echo "Accelya M1 - environment check"

command -v git >/dev/null && pass "git $(git --version | awk '{print $3}')" || fail "git" "install Git"
PY=$(command -v python3 || command -v python)
if [ -n "$PY" ] && $PY -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)'; then
  pass "python $($PY -V 2>&1 | awk '{print $2}')"; else fail "python >= 3.11" "install Python 3.11+"; fi
$PY -c 'import boto3' 2>/dev/null && pass "boto3 $($PY -c 'import boto3;print(boto3.__version__)')" \
  || fail "boto3" "python3 -m pip install -U boto3"
command -v aws >/dev/null && pass "aws cli $(aws --version 2>&1 | awk '{print $1}')" || fail "aws cli v2" "install AWS CLI v2"
SAM=$(command -v sam || command -v sam.cmd)   # Git Bash on Windows only finds sam.cmd
[ -n "$SAM" ] && pass "sam cli $("$SAM" --version | awk '{print $NF}')" || fail "sam cli" "install AWS SAM CLI"

REGION=$(aws configure get region 2>/dev/null || true); REGION=${AWS_DEFAULT_REGION:-$REGION}
case "$REGION" in us-east-1|us-west-2) pass "region $REGION";; *) fail "region is '${REGION:-unset}'" "source day1/scripts/env.sh <id>  (sets us-east-1)";; esac

AI=$(aws sts get-caller-identity --profile accelya-ai --query Arn --output text 2>/dev/null)
[ -n "$AI" ] && pass "AI sandbox profile accelya-ai: $AI" || fail "profile accelya-ai (Lab 1)" "aws configure --profile accelya-ai  (AWS AI cloud sandbox keys)"
ID=$(aws sts get-caller-identity --profile accelya --query Arn --output text 2>/dev/null)
[ -n "$ID" ] && pass "standard sandbox profile accelya: $ID" || echo "  INFO  profile accelya (standard sandbox) not set yet - needed from Lab 2"

if [ -z "$ID" ]; then :; elif aws iam get-role --profile accelya --role-name accelya-nonexistent-probe >/dev/null 2>&1; then :; else
  ERR=$(aws iam get-role --profile accelya --role-name accelya-nonexistent-probe 2>&1)
  echo "$ERR" | grep -q NoSuchEntity && pass "iam:GetRole allowed (IAM readable)" \
    || echo "  INFO  IAM read is restricted in this sandbox - tell the trainer (fallback mode may be needed)"
fi

command -v docker >/dev/null && pass "docker (needed on Day 5)" || echo "  INFO  docker not found - needed on Day 5 only"
echo
[ $FAILS -eq 0 ] && echo "All required checks passed. You are ready for Lab 1." || echo "$FAILS required check(s) failed - fix before Lab 1."
