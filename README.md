# Accelya - Agentic Development using AWS (Nov-Dec 2026)

Course repository for the 10 half-day programme delivered by Jai Singh for Accelya Kale.
Region `ap-south-1`. Synthetic airline data only - no real customer data, ever.

## Day 1 in one screen

```bash
git clone https://github.com/jaisingh-builds/accelya-agentic-dev-nov26.git
cd accelya-agentic-dev-nov26
git checkout -b day1-<your-id>                 # e.g. day1-priya
bash day1/scripts/verify_setup.sh              # all PASS before Lab 1

# Lab 1 - prompt -> two architectures (Bedrock)
python3 day1/scripts/arch_options.py --learner <your-id>

# Lab 2 - deploy S3 -> Lambda -> CloudWatch
cd day1
sam build
sam deploy --stack-name accelya-m1-<your-id> --region ap-south-1 \
  --capabilities CAPABILITY_NAMED_IAM --resolve-s3 \
  --parameter-overrides LearnerId=<your-id>
```

Full steps: see the Day 1 slides (Lab 1-3 playbooks).

## Layout

| Path | What |
|---|---|
| `AGENTS.md` | Context every AI assistant must read first |
| `docs/service-boundary-6q.md` | The six questions for every service hop |
| `docs/HITL_REVIEW_TEMPLATE.md` | Human review checklist - required before merge |
| `glossary/airline.md` | Airline vocabulary |
| `day1/` | Module 1 code, data, prompts, scripts |

## Rules of the road
- Work on your own branch `dayN-<your-id>`. Push your branch; open a PR into `main` only when asked.
- Never commit AWS keys, `.aws/` files or `.env` files.
- Run `bash day1/scripts/cleanup_day1.sh <your-id>` before you leave.
