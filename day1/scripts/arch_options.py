#!/usr/bin/env python3
"""Day 1 Lab 1 - ask Bedrock for TWO architecture options for a business requirement.

Usage:
    python3 day1/scripts/arch_options.py --learner priya
    python3 day1/scripts/arch_options.py --learner priya --prompt day1/prompts/my-prompt.md

Writes day1/outputs/m1-arch-options-<learner>.md and prints token usage.
Sandbox budget: max 50 Bedrock calls / 20,000 tokens per learner for the WHOLE course.
This script refuses a 4th call on Day 1 unless you pass --force.
"""
import argparse
import os
import sys
from pathlib import Path

try:
    import boto3
    from botocore.exceptions import ClientError
except ImportError:
    sys.exit("boto3 missing. Fix: python3 -m pip install -U boto3")

REGION = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
MODEL_ID = os.getenv("BEDROCK_MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0")
DAY1 = Path(__file__).resolve().parent.parent
COUNTER = DAY1 / "outputs" / ".bedrock_calls"
MAX_DAY1_CALLS = 3

SYSTEM = (
    "You are a senior AWS architect for an airline settlement platform. "
    "Use airline vocabulary (PNR, ticket_number, fare_class, od_pair, settlement). "
    "Return exactly TWO architecture options in Markdown. For each option give: "
    "1) a one-line summary, 2) the AWS services in order, 3) for EVERY hop between two services "
    "a table answering: push or pull | sync or async | payload shape | which IAM role | "
    "failure path | where the correlation_id lives, 4) two risks. "
    "End with a 3-line comparison. No preamble. Max 600 words."
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--learner", required=True)
    ap.add_argument("--prompt", default=str(DAY1 / "prompts" / "m1-arch-options.md"))
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--profile", default=os.getenv("BEDROCK_PROFILE", "accelya-ai"),
                    help="AWS profile of the Pluralsight AWS *AI* cloud sandbox (Bedrock lives there)")
    args = ap.parse_args()

    calls = int(COUNTER.read_text()) if COUNTER.exists() else 0
    if calls >= MAX_DAY1_CALLS and not args.force:
        sys.exit(f"Budget guard: {calls} Bedrock calls already made today. Re-use your output, or pass --force.")

    prompt = Path(args.prompt).read_text()
    if "<FILL" in prompt:
        sys.exit(f"Edit {args.prompt} first - replace every <FILL ...> marker.")

    print(f"region={REGION} model={MODEL_ID} learner={args.learner}\n")
    try:
        session = boto3.Session(profile_name=args.profile)
    except Exception:
        sys.exit(f"Profile '{args.profile}' not found. Run: aws configure --profile {args.profile}  (AI sandbox keys, us-east-1)")
    print(f"profile={args.profile}")
    client = session.client("bedrock-runtime", region_name=REGION)
    try:
        resp = client.converse(
            modelId=MODEL_ID,
            system=[{"text": SYSTEM}],
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": 1200, "temperature": 0.3},
        )
    except ClientError as e:
        code = e.response["Error"]["Code"]
        hints = {
            "AccessDeniedException": "Model access not granted in this sandbox - tell the trainer.",
            "ValidationException": "Model id/profile not enabled here. List what is: aws bedrock list-inference-profiles --query \"inferenceProfileSummaries[].inferenceProfileId\" then export BEDROCK_MODEL_ID=<one of them>",
            "ThrottlingException": "Throttled - wait 30 s and retry once.",
        }
        sys.exit(f"Bedrock error {code}: {e.response['Error']['Message']}\nHint: {hints.get(code, 'show the trainer')}")

    COUNTER.parent.mkdir(exist_ok=True)
    COUNTER.write_text(str(calls + 1))

    text = resp["output"]["message"]["content"][0]["text"]
    usage = resp["usage"]
    out = DAY1 / "outputs" / f"m1-arch-options-{args.learner}.md"
    out.write_text(
        f"# M1 architecture options - {args.learner}\n\n"
        f"_model: {MODEL_ID} | tokens in/out: {usage['inputTokens']}/{usage['outputTokens']}_\n\n"
        f"## Prompt\n\n{prompt}\n\n## Bedrock answer\n\n{text}\n",
        encoding="utf-8",
    )
    print(text)
    print(f"\n---\ntokens in={usage['inputTokens']} out={usage['outputTokens']} "
          f"total={usage['totalTokens']} | calls today={calls + 1}/{MAX_DAY1_CALLS}")
    print(f"saved -> {out.relative_to(DAY1.parent)}")


if __name__ == "__main__":
    main()
