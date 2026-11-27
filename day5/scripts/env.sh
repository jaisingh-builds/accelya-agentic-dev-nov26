# Day 5 shell setup. Run from the repo root in every new terminal:
#     source day5/scripts/env.sh <your-id>
# Reuses Day 1's setup (profile, region, L, F, ACCT), then names everything in the Day 5 stack.
. day1/scripts/env.sh "$1" >/dev/null || return 1 2>/dev/null
export S="accelya-m5-${L}"
export B="accelya-m5-${L}-${ACCT}"
export REPO="accelya-m5-${L}-enrich"
export ECR="${ACCT}.dkr.ecr.${AWS_DEFAULT_REGION}.amazonaws.com"
export IMG="${ECR}/${REPO}:v1"
export CL="accelya-m5-${L}"
export TD="accelya-m5-${L}-enrich"
export LG="/ecs/accelya-m5-${L}-enrich"
export WG="accelya-m5-${L}"
export GDB="accelya_m5_$(echo "$L" | tr '-' '_')"
# Network ids come from the stack once it exists (empty before Lab 2's deploy: source this again after it).
export SUBNET=$(aws cloudformation describe-stacks --stack-name "$S" --query "Stacks[0].Outputs[?OutputKey=='SubnetId'].OutputValue" --output text 2>/dev/null)
export SG=$(aws cloudformation describe-stacks --stack-name "$S" --query "Stacks[0].Outputs[?OutputKey=='SecurityGroupId'].OutputValue" --output text 2>/dev/null)
echo "profile=${AWS_PROFILE:-default}  region=$AWS_DEFAULT_REGION  L=$L"
echo "S=$S  B=$B"
echo "IMG=$IMG"
echo "CL=$CL  TD=$TD  LG=$LG"
echo "WG=$WG  GDB=$GDB  SUBNET=${SUBNET:-<deploy first>}  SG=${SG:-<deploy first>}"
