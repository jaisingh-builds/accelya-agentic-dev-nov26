# Accelya - Agentic Development using AWS (Nov-Dec 2026)

Course repository for the 10 half-day programme delivered by Jai Singh for Accelya Kale.
Region `us-east-1` (Pluralsight sandbox). Synthetic airline data only - no real customer data, ever.

## Day 1 in one screen

```bash
aws configure --profile accelya-ai              # AWS AI cloud sandbox (Bedrock) - Lab 1
aws configure --profile accelya                 # standard AWS sandbox - Labs 2-3
git clone https://github.com/jaisingh-builds/accelya-agentic-dev-nov26.git
cd accelya-agentic-dev-nov26
source day1/scripts/env.sh <your-id>          # sets L, F, B, LG
git checkout -b day1-$L
bash day1/scripts/verify_setup.sh              # all PASS before Lab 1

# Lab 1 - prompt -> two architectures (Bedrock)
python3 day1/scripts/arch_options.py --learner $L      # uses profile accelya-ai

# Lab 2 - change the handler, test, then deploy S3 -> Lambda -> CloudWatch
python3 day1/tests/test_ingest.py              # 4 PASS before you deploy
cd day1 && sam build
sam deploy --stack-name accelya-m1-$L $F --parameter-overrides LearnerId=$L
```

Full steps: see the Day 1 slides (Lab 1-3 playbooks).

## Day 2 in one screen

```bash
# fresh laptop? clone, then create the venv once
git clone https://github.com/jaisingh-builds/accelya-agentic-dev-nov26.git && cd accelya-agentic-dev-nov26
python3 -m venv .venv && source .venv/bin/activate          # Windows Git Bash: source .venv/Scripts/activate
python3 -m pip install -U -r requirements.txt

# every day
source .venv/bin/activate
source day1/scripts/env.sh <your-id>                         # sets L
git checkout main && git pull && git checkout -b day2-$L     # fresh branch from the latest main

# Lab 1 needs the AWS AI cloud sandbox keys in profile accelya-ai
aws configure --profile accelya-ai                           # + aws configure set aws_session_token <TOKEN> --profile accelya-ai if shown
python3 day1/scripts/arch_options.py --learner $L --profile accelya-ai --options 3 \
    --prompt day2/prompts/m2-arch-variants.md --out-dir day2/outputs

# Labs 2-3 run locally, with AWS credentials removed by the harness
python3 day2/demo/weak_cancellation.py
python3 day2/tests/run_and_check.py                          # build day2/src/cancellation/handler.py until 6/6
python3 day2/tests/test_checker.py                           # proves the log checker catches hidden payloads
```

All data in this repo is synthetic. Never paste real customer data into prompts, logs or reviews.

## Layout

| Path | What |
|---|---|
| `AGENTS.md` | Context every AI assistant must read first |
| `docs/service-boundary-6q.md` | The six questions for every service hop |
| `docs/HITL_REVIEW_TEMPLATE.md` | Human review checklist - required before merge |
| `docs/LOGGING_CONTRACT.md` | The logging standard every artefact is reviewed against (from Day 2) |
| `glossary/airline.md` | Airline vocabulary |
| `day1/`, `day2/` | Per-module code, data, prompts, scripts, tests |
| `slides/` | Day slides (pptx + pdf) |

## Rules of the road
- Work on your own branch `dayN-<your-id>`. Push your branch; open a PR into `main` only when asked.
- Never commit AWS keys, `.aws/` files or `.env` files.
- Run `bash day1/scripts/cleanup_day1.sh <your-id>` before you leave.
