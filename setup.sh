#!/usr/bin/env bash
# Lab setup - run this at the start of EVERY terminal session, before any lab:
#
#     source ./setup.sh <learner-id>        # e.g.  source ./setup.sh chandra
#
# It must be SOURCED (not `bash setup.sh`) so the venv and env vars persist.
# Does: Git Bash path fix, venv activate + pip install, env vars (env.sh),
# AWS credential check, tool check, git status summary.

L=${1:-}
if [ -z "$L" ]; then echo "usage: source ./setup.sh <learner-id>"; return 1 2>/dev/null || exit 1; fi
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  echo "Source me instead, so the venv and env vars stick:  source ./setup.sh $L"
  exit 1
fi
die() { echo "$@"; return 1 2>/dev/null || exit 1; }

# 1. Git Bash must not rewrite /aws/lambda/... args into Windows paths.
export MSYS_NO_PATHCONV=1

# 2. Python venv (Windows keeps it in Scripts/, Unix in bin/).
if [ -f .venv/Scripts/activate ]; then
  source .venv/Scripts/activate
  # Windows venvs have no python3.exe; without it, `python3` escapes the venv
  # and hits ~/bin/python3 (a shim pinned to a different interpreter).
  [ -f .venv/Scripts/python3.exe ] || cp .venv/Scripts/python.exe .venv/Scripts/python3.exe 2>/dev/null
elif [ -f .venv/bin/activate ]; then
  source .venv/bin/activate
else
  die "No .venv found. Create it first:  python -m venv .venv"
fi
PYVER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null)
echo "venv python: $PYVER"
if [ "$PYVER" != "3.12" ]; then
  echo "WARNING: course standard is Python 3.12; pyarrow/powertools may not install on $PYVER."
  echo "         Recreate:  mv .venv .venv-py${PYVER:-old} && py -3.12 -m venv .venv && source ./setup.sh $L"
fi

# 3. Dependencies (idempotent; quick when unchanged).
pip install -q -r requirements.txt || die "pip install failed - check requirements.txt"

# 4. Course env vars: L, B, LG, F, AWS_PROFILE, AWS_DEFAULT_REGION, BEDROCK_MODEL_ID.
source day1/scripts/env.sh "$L" || die "env.sh failed"
case "$B" in *"<"*) echo "NOTE: standard sandbox not reachable; B uses <acct> placeholder.";; esac

# 5. Credential check - which sandbox profiles are alive right now?
echo
echo "AWS credentials:"
for P in accelya accelya-ai; do
  ACCT=$(aws sts get-caller-identity --profile "$P" --query Account --output text 2>/dev/null) \
    && echo "  $P: OK  account ...${ACCT: -4}" \
    || echo "  $P: expired or missing  (fix: aws configure --profile $P)"
done

# 6. Tool check.
echo
echo "Tools:"
for T in aws sam python3 git; do
  command -v "$T" >/dev/null 2>&1 && echo "  $T: OK" || echo "  $T: MISSING"
done

# 7. Repo state at a glance.
echo
echo "Git: branch $(git branch --show-current), $(git status --porcelain | wc -l | tr -d ' ') uncommitted path(s)"
echo
echo "Ready. L=$L  B=$B"
