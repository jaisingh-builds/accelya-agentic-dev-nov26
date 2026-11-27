# Day 4 shell setup. Run from the repo root in every new terminal:
#     source day4/scripts/env.sh <your-id>
# Reuses Day 1's setup (profile, region, L, F), then points B, LG, FN and Q at the Day 4 stack.
. day1/scripts/env.sh "$1" >/dev/null || return 1 2>/dev/null
export S="accelya-m4-${L}"
export B="accelya-m4-${L}-${ACCT}"
export FN="accelya-m4-${L}-processor"
export LG="/aws/lambda/${FN}"
export Q="accelya-m4-${L}-processor-dlq"
echo "profile=${AWS_PROFILE:-default}  region=$AWS_DEFAULT_REGION  L=$L"
echo "S=$S  B=$B"
echo "FN=$FN  LG=$LG  Q=$Q"
