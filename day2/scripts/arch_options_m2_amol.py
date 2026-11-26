#!/usr/bin/env python3
"""Day 2 Lab 1 - generate architecture options for the cancellation refund decision flow.

Usage (from repo root):
    python3 day2/scripts/arch_options_m2_amol.py --learner amol.more

Token budget: each call should stay at or below 1,500 tokens total.
Set BEDROCK_MAX_TOKENS to override the output cap (default 1200).
"""
import argparse
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    import boto3
    from botocore.exceptions import ClientError
except ImportError:
    sys.exit("boto3 missing. Fix: python3 -m pip install -U boto3")

ROOT = Path(__file__).resolve().parents[2]
SAMPLE_PATH = ROOT / "day2" / "samples" / "m2-arch-variants-sample.md"
USAGE_LOG = ROOT.parent / "bedrock_usage.md"
BUDGET_TOTAL = 1500
MAX_TOKENS_DEFAULT = 1200

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
Max 500 words. Table cells max 6 words.
"""


def _log_usage(learner, model, input_tokens, output_tokens, source):
    """Append one line to the usage log outside the repo."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    total = input_tokens + output_tokens
    line = f"| {ts} | day2 | {learner} | {model} | {input_tokens} | {output_tokens} | {total} | {source} |\n"
    if not USAGE_LOG.exists():
        header = "| ts | module | learner | model | in | out | total | source |\n|---|---|---|---|---|---|---|---|\n"
        USAGE_LOG.write_text(header + line, encoding="utf-8")
    else:
        with open(USAGE_LOG, "a", encoding="utf-8") as f:
            f.write(line)
    print(f"Usage logged -> {USAGE_LOG}")


def _check_model_access(client, model_id):
    """List inference profiles to verify model access without spending tokens."""
    try:
        resp = client.list_inference_profiles() if hasattr(client, "list_inference_profiles") else None
        if resp:
            ids = [p["inferenceProfileId"] for p in resp.get("inferenceProfileSummaries", [])]
            if model_id in ids:
                print(f"Model access verified (free check): {model_id}")
                return True
            print(f"Model {model_id} not in inference profiles: {ids[:5]}...")
            return False
    except Exception:
        pass
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--learner", default="amol.more")
    ap.add_argument("--model", default="anthropic.claude-sonnet-4-6")
    ap.add_argument("--profile", default="accelya-ai")
    ap.add_argument("--use-sample", action="store_true",
                    help="skip the Bedrock call and score the pre-written sample instead")
    ap.add_argument("--force", action="store_true",
                    help="skip the budget confirmation prompt")
    args = ap.parse_args()

    max_tokens = int(os.getenv("BEDROCK_MAX_TOKENS", str(MAX_TOKENS_DEFAULT)))
    estimated_input = len(PROMPT.split()) * 2
    estimated_total = estimated_input + max_tokens

    out_dir = ROOT / "day2" / "outputs"
    out_dir.mkdir(exist_ok=True)
    out_file = out_dir / f"m2-arch-options-{args.learner}.md"

    if args.use_sample:
        if not SAMPLE_PATH.exists():
            sys.exit(f"Sample not found: {SAMPLE_PATH}")
        text = SAMPLE_PATH.read_text(encoding="utf-8")
        out_file.write_text(text, encoding="utf-8")
        _log_usage(args.learner, "sample", 0, 0, "sample-fallback")
        print(f"Sample copied -> {out_file.relative_to(ROOT)}")
        print("Score this sample with the scorecard template instead of making a Bedrock call.")
        return

    print(f"model={args.model}  max_tokens={max_tokens}  estimated_total~{estimated_total}")
    if estimated_total > BUDGET_TOTAL and not args.force:
        print(f"WARNING: estimated {estimated_total} tokens exceeds the {BUDGET_TOTAL}-token budget.")
        answer = input("Continue? [y/N] ").strip().lower()
        if answer != "y":
            print("Aborted. Use --use-sample to score the pre-written sample, or --force to skip this check.")
            return

    session = boto3.Session(profile_name=args.profile, region_name="us-east-1")
    client = session.client("bedrock-runtime")

    bedrock_client = session.client("bedrock", region_name="us-east-1")
    if not _check_model_access(bedrock_client, args.model):
        if SAMPLE_PATH.exists():
            print(f"Model not accessible. Falling back to sample: {SAMPLE_PATH}")
            text = SAMPLE_PATH.read_text(encoding="utf-8")
            out_file.write_text(text, encoding="utf-8")
            _log_usage(args.learner, args.model, 0, 0, "access-denied-fallback")
            print(f"Saved -> {out_file.relative_to(ROOT)}")
            return
        sys.exit("Model not accessible and no sample file found.")

    print(f"Calling Bedrock ({args.model}) for learner {args.learner}...")

    try:
        response = client.converse(
            modelId=args.model,
            messages=[{"role": "user", "content": [{"text": PROMPT}]}],
            inferenceConfig={"maxTokens": max_tokens, "temperature": 0.3},
        )
    except ClientError as e:
        code = e.response["Error"]["Code"]
        print(f"Bedrock error {code}: {e.response['Error']['Message']}")
        if SAMPLE_PATH.exists():
            print(f"Falling back to sample: {SAMPLE_PATH}")
            text = SAMPLE_PATH.read_text(encoding="utf-8")
            out_file.write_text(text, encoding="utf-8")
            _log_usage(args.learner, args.model, 0, 0, f"error-fallback-{code}")
            print(f"Saved -> {out_file.relative_to(ROOT)}")
            return
        sys.exit("No sample fallback available.")

    text = response["output"]["message"]["content"][0]["text"]
    usage = response.get("usage", {})
    input_tokens = usage.get("inputTokens", 0)
    output_tokens = usage.get("outputTokens", 0)
    total_tokens = input_tokens + output_tokens

    if response.get("stopReason") == "max_tokens":
        text += "\n\n_[answer cut off at the token limit: score what is here and note it as a finding]_"
        print("WARNING: answer hit the token limit and is cut off.")

    print(f"Tokens: input={input_tokens}, output={output_tokens}, total={total_tokens}")
    if total_tokens > BUDGET_TOTAL:
        print(f"WARNING: total {total_tokens} exceeded the {BUDGET_TOTAL}-token budget.")

    header = (
        f"# Architecture options (3) — M2 cancellation refund decision\n\n"
        f"Learner: {args.learner}\n"
        f"Model: {args.model}\n"
        f"Tokens: in={input_tokens} out={output_tokens} total={total_tokens}\n\n"
    )
    out_file.write_text(header + text, encoding="utf-8")
    _log_usage(args.learner, args.model, input_tokens, output_tokens, "bedrock-call")
    print(f"Saved to {out_file.relative_to(ROOT)}")
    print("\n" + text.encode("ascii", "replace").decode())


if __name__ == "__main__":
    main()
