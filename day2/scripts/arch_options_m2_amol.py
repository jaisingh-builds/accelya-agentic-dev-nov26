#!/usr/bin/env python3
"""Day 2 Lab 1 - generate architecture options for the cancellation refund decision flow.

Usage (from repo root):
    python3 day2/scripts/arch_options_m2.py --learner amol.more
"""
import argparse
import json
import boto3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

PROMPT = """\
## ROLE
You are a senior AWS architect for Accelya's airline settlement platform (region us-east-1).

## CONTEXT
Business flow: a passenger or agent cancels a ticket. The cancellation event (pnr, ticket_number, fare_class,
departure_status, no_show_flag, fare_amount) must get a refund decision (APPROVE / REJECT / MANUAL_REVIEW)
and the settlement team must see every decision.
Volume: 2,000 cancellations/day, peaks of 50/minute after a flight disruption.
Ordering: yes, order matters per PNR — a re-booking must not overtake its cancellation.

## EXAMPLES
One event: {"event_id": "evt-1002", "pnr": "LM4K8D", "fare_class": "K", "departure_status": "scheduled",
"no_show_flag": false, "fare_amount": "4120.00"}  ->  APPROVE, R-SAVER, refund 3090.00

## NON-GOALS
- No Kinesis, no MSK, no EC2 or servers to patch.
- Never log passenger names, emails or fare amounts.
- No Step Functions in this module.

## CODING AND NAMING RULES (from AGENTS.md)
- Every resource name starts accelya-m2-<learner>-.
- IAM: named actions on ARN-scoped resources. Never "Action": "*" or "Resource": "*".
- No secrets, keys or account ids in code.
- Every log line is ONE JSON object with level, service, correlation_id, message.
- Never log file contents, full payloads, PNR lists or fare amounts - log identifiers and counts only.

## SERVICE-BOUNDARY QUESTIONS (answer for every hop)
1. Push or pull?
2. Sync or async?
3. Payload shape?
4. Which IAM role and minimum actions?
5. Failure path?
6. Correlation ID?

## REFUND RULES (day2/data/refund_rules.json)
First match wins:
- R-NOSHOW: no_show_flag is true -> REJECT, 0%
- R-DEPARTED: departure_status == 'departed' -> REJECT, 0%
- R-FLEX: fare family FLEX (Y, B, M) -> APPROVE, 100%
- R-BUSINESS: fare family BUSINESS (J, C, D) -> APPROVE, 100%
- R-SAVER: fare family SAVER (K, L, Q, V) -> APPROVE, 75%
- R-UNKNOWN: fare_class in no family -> MANUAL_REVIEW, 0%
refund_amount = fare_amount * refund_pct / 100 with Decimal, ROUND_HALF_UP, 2 dp, as a string.

## OUTPUT FORMAT
Exactly THREE architecture options. For each: a one-line summary, the services in order, the six
service-boundary answers for every hop (as a table), two risks, and a rough cost driver. End with a comparison table.
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--learner", default="amol.more")
    ap.add_argument("--model", default="anthropic.claude-sonnet-4-6")
    ap.add_argument("--profile", default="accelya-ai")
    args = ap.parse_args()

    session = boto3.Session(profile_name=args.profile, region_name="us-east-1")
    client = session.client("bedrock-runtime")

    print(f"Calling Bedrock ({args.model}) for learner {args.learner}...")

    response = client.converse(
        modelId=args.model,
        messages=[
            {
                "role": "user",
                "content": [{"text": PROMPT}],
            }
        ],
        inferenceConfig={"maxTokens": 4096, "temperature": 0.3},
    )

    text = response["output"]["message"]["content"][0]["text"]
    usage = response.get("usage", {})
    print(f"Tokens: input={usage.get('inputTokens', '?')}, output={usage.get('outputTokens', '?')}")

    out_dir = ROOT / "day2" / "outputs"
    out_dir.mkdir(exist_ok=True)
    out_file = out_dir / f"m2-arch-options-{args.learner}.md"
    header = f"# Architecture options (3) — M2 cancellation refund decision\n\nLearner: {args.learner}\nModel: {args.model}\n\n"
    out_file.write_text(header + text, encoding="utf-8")
    print(f"Saved to {out_file.relative_to(ROOT)}")
    print("\n" + text.encode("ascii", "replace").decode())


if __name__ == "__main__":
    main()
