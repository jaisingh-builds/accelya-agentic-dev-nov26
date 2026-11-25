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
# First model that this account can call wins. Override with BEDROCK_MODEL_ID=<inference profile id>.
MODEL_IDS = [m for m in [os.getenv("BEDROCK_MODEL_ID")] if m] + [
    "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    "us.anthropic.claude-sonnet-4-6",
    "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    "us.anthropic.claude-3-haiku-20240307-v1:0",
]
MODEL_ID = MODEL_IDS[0]
DAY1 = Path(__file__).resolve().parent.parent
COUNTER = DAY1 / "outputs" / ".bedrock_calls"
MAX_DAY1_CALLS = 3

SYSTEM = (
    "You are a senior AWS architect for an airline settlement platform. "
    "Use airline vocabulary (PNR, ticket_number, fare_class, od_pair, settlement). "
    "Return exactly {n} architecture options in Markdown. For each option give: "
    "1) a one-line summary, 2) the AWS services in order, 3) for EVERY hop between two services "
    "a table answering: push or pull | sync or async | payload shape | which IAM role | "
    "failure path | where the correlation_id lives, 4) two risks. "
    "Keep tables tight: one row per hop, at most 8 words per cell. "
    "End with a short comparison table. No preamble. Max {words} words."
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--learner", required=True)
    ap.add_argument("--prompt", default=str(DAY1 / "prompts" / "m1-arch-options.md"))
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--options", type=int, choices=(2, 3), default=2, help="how many architecture options")
    ap.add_argument("--out-dir", default=None, help="where to save (default: day1/outputs)")
    ap.add_argument("--profile", default=os.getenv("BEDROCK_PROFILE", "accelya-ai"),
                    help="profile to try first (default accelya-ai; 'auto' = accelya-ai then accelya; 'default' = default credentials)")
    args = ap.parse_args()

    out_dir = Path(args.out_dir).resolve() if args.out_dir else DAY1 / "outputs"
    counter = out_dir / ".bedrock_calls"
    calls = int(counter.read_text()) if counter.exists() else 0
    if calls >= MAX_DAY1_CALLS and not args.force:
        sys.exit(f"Budget guard: {calls} Bedrock calls already made today. Re-use your output, or pass --force.")

    prompt = Path(args.prompt).read_text(encoding="utf-8")
    if "<FILL" in prompt:
        sys.exit(f"Edit {args.prompt} first - replace every <FILL ...> marker.")

    print(f"region={REGION} learner={args.learner}")
    # Use the first sandbox profile whose credentials actually work: --profile, then accelya-ai, accelya.
    # Default credentials are used ONLY when asked for explicitly (--profile default): on a laptop they
    # are often a different (company or personal) account.
    session = None
    tried = []
    first = [] if args.profile in ("", "auto") else [args.profile]
    for prof in dict.fromkeys(first + ["accelya-ai", "accelya"]):
        prof = None if prof == "default" else prof
        name = prof or "default credentials"
        try:
            cand = boto3.Session(profile_name=prof) if prof else boto3.Session()
            ident = cand.client("sts", region_name=REGION).get_caller_identity()
        except Exception as e:  # noqa: BLE001 - missing profile, expired token, no keys
            tried.append(f"{name}: {type(e).__name__}")
            continue
        session, args.profile = cand, name
        print(f"profile={name} account=...{ident['Account'][-4:]}")
        break
    if session is None:
        sys.exit("No working AWS credentials (" + "; ".join(tried) + ").\n"
                 "Run: aws configure --profile accelya-ai  (AI sandbox keys, us-east-1; add the session token if shown)")
    client = session.client("bedrock-runtime", region_name=REGION)
    resp, MODEL_ID, skipped = None, None, []
    for model in MODEL_IDS:
      try:
        resp = client.converse(
            modelId=model,
            system=[{"text": SYSTEM.format(n="TWO" if args.options == 2 else "THREE",
                                           words=600 if args.options == 2 else 850)}],
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": 2500 if args.options == 2 else 3500, "temperature": 0.3},
        )
        MODEL_ID = model
        break
      except ClientError as e:
        code = e.response["Error"]["Code"]
        if code in ("ResourceNotFoundException", "ValidationException", "AccessDeniedException") and model != MODEL_IDS[-1]:
            skipped.append(f"{model} ({code})")
            continue
        hints = {
            "AccessDeniedException": "Model access not granted in this sandbox - tell the trainer.",
            "ValidationException": "Model id/profile not enabled here. List what is: aws bedrock list-inference-profiles --query \"inferenceProfileSummaries[].inferenceProfileId\" then export BEDROCK_MODEL_ID=<one of them>",
            "ThrottlingException": "Throttled - wait 30 s and retry once.",
        }
        sys.exit(("Skipped: " + "; ".join(skipped) + "\n" if skipped else "") +
                 f"Bedrock error {code}: {e.response['Error']['Message']}\nHint: {hints.get(code, 'show the trainer')}")
    if skipped:
        print("skipped (not available here): " + "; ".join(skipped))
    print(f"model={MODEL_ID}\n")

    out_dir.mkdir(parents=True, exist_ok=True)
    counter.write_text(str(calls + 1))

    text = resp["output"]["message"]["content"][0]["text"]
    if resp.get("stopReason") == "max_tokens":
        text += "\n\n_[answer cut off at the token limit: score what is here and note it as a finding]_"
        print("WARNING: the answer hit the token limit and is cut off.")
    usage = resp["usage"]
    stem = Path(args.prompt).stem.replace("-prompt", "")
    out = out_dir / (f"m1-arch-options-{args.learner}.md" if args.out_dir is None else f"{stem}-{args.learner}.md")
    out.write_text(
        f"# Architecture options ({args.options}) - {args.learner}\n\n"
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
