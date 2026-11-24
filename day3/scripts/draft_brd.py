#!/usr/bin/env python3
"""Day 3 Lab 4 - ask Bedrock to draft (or revise) a BRD from day3/prompts/m3-brd-prompt.md.

Usage (repo root):
    python3 day3/scripts/draft_brd.py --learner priya                 # draft 1
    python3 day3/scripts/draft_brd.py --learner priya --draft 2       # after filling the REVISION section

Writes day3/outputs/m3-brd-<learner>-v<draft>.md and prints token usage.
Budget guard: 3 calls a day in day3/outputs (course budget: 50 calls / 20,000 tokens per learner).
Credentials: the first working profile of --profile, accelya-ai, accelya. Default credentials only with
--profile default (on a laptop they're often a different account).
"""
import argparse
import os
import sys
from pathlib import Path

try:
    import boto3
    from botocore.exceptions import ClientError
except ImportError:
    sys.exit("boto3 missing. Activate the venv: source .venv/bin/activate, then pip install -r requirements.txt")

REGION = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
MODEL_IDS = [m for m in [os.getenv("BEDROCK_MODEL_ID")] if m] + [
    "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    "us.anthropic.claude-sonnet-4-6",
    "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    "us.anthropic.claude-3-haiku-20240307-v1:0",
]
DAY3 = Path(__file__).resolve().parent.parent
ROOT = DAY3.parent
MAX_CALLS = 3
SYSTEM = ("You write Business Requirements Documents for airline data platforms. Follow the user's OUTPUT FORMAT "
          "exactly. Requirements only: never name AWS services or write code. If a REVISION section has notes, "
          "apply every note and change nothing else. No preamble.")


def session_for(profile):
    tried = []
    first = [] if profile in ("", "auto") else [profile]
    for prof in dict.fromkeys(first + ["accelya-ai", "accelya"]):
        prof = None if prof == "default" else prof
        name = prof or "default credentials"
        try:
            s = boto3.Session(profile_name=prof) if prof else boto3.Session()
            ident = s.client("sts", region_name=REGION).get_caller_identity()
        except Exception as e:  # noqa: BLE001 - missing profile, expired token, no keys
            tried.append(f"{name}: {type(e).__name__}")
            continue
        print(f"profile={name} account=...{ident['Account'][-4:]}")
        return s
    sys.exit("No working AWS credentials (" + "; ".join(tried) + ").\n"
             "Run: aws configure --profile accelya-ai  (AI sandbox keys, us-east-1; add the session token if shown)")


def main():
    ap = argparse.ArgumentParser(description="Draft or revise the Day 3 BRD with Bedrock")
    ap.add_argument("--learner", required=True)
    ap.add_argument("--prompt", default=str(DAY3 / "prompts" / "m3-brd-prompt.md"))
    ap.add_argument("--draft", type=int, choices=(1, 2, 3), default=1)
    ap.add_argument("--out-dir", default=str(DAY3 / "outputs"))
    ap.add_argument("--profile", default=os.getenv("BEDROCK_PROFILE", "accelya-ai"),
                    help="profile to try first (default accelya-ai; 'auto' = accelya-ai then accelya; 'default' = default credentials)")
    ap.add_argument("--force", action="store_true", help="ignore the 3-calls-a-day guard")
    args = ap.parse_args()

    prompt = Path(args.prompt).read_text()
    if "<FILL" in prompt:
        sys.exit(f"Edit {args.prompt} first - replace every <FILL ...> marker.")
    revision = prompt.split("## REVISION", 1)[1] if "## REVISION" in prompt else ""
    has_notes = any(l.strip() and not l.strip().startswith("(") for l in revision.splitlines())
    if args.draft > 1 and not has_notes:
        sys.exit("Draft 2 needs revision notes: paste them under ## REVISION in the prompt first.")
    if args.draft == 1 and has_notes:
        print("note: the REVISION section has notes, so this draft 1 will already apply them.")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    counter = out_dir / ".bedrock_calls"
    calls = int(counter.read_text()) if counter.exists() else 0
    if calls >= MAX_CALLS and not args.force:
        sys.exit(f"Budget guard: {calls} Bedrock calls already made from {out_dir}. Reuse your draft, or pass --force.")

    print(f"region={REGION} learner={args.learner} draft={args.draft}")
    client = session_for(args.profile).client("bedrock-runtime", region_name=REGION)
    resp, model_used, skipped = None, None, []
    for model in MODEL_IDS:
        try:
            resp = client.converse(modelId=model, system=[{"text": SYSTEM}],
                                   messages=[{"role": "user", "content": [{"text": prompt}]}],
                                   inferenceConfig={"maxTokens": 3000, "temperature": 0.2})
            model_used = model
            break
        except ClientError as e:
            code = e.response["Error"]["Code"]
            if code in ("ResourceNotFoundException", "ValidationException", "AccessDeniedException") and model != MODEL_IDS[-1]:
                skipped.append(f"{model} ({code})")
                continue
            sys.exit(("Skipped: " + "; ".join(skipped) + "\n" if skipped else "") +
                     f"Bedrock error {code}: {e.response['Error']['Message']}\n"
                     "Hint: use the published drafts instead: day3/samples/m3-brd-sample-v1.md and -v2.md")
    if skipped:
        print("skipped (not available here): " + "; ".join(skipped))
    print(f"model={model_used}\n")
    counter.write_text(str(calls + 1))

    text = resp["output"]["message"]["content"][0]["text"]
    if resp.get("stopReason") == "max_tokens":
        text += "\n\n_[cut off at the token limit: note it as a finding]_"
        print("WARNING: the draft hit the token limit and is cut off.")
    usage = resp["usage"]
    out = out_dir / f"m3-brd-{args.learner}-v{args.draft}.md"
    out.write_text(f"# BRD draft v{args.draft} - {args.learner}\n\n"
                   f"_model: {model_used} | tokens in/out: {usage['inputTokens']}/{usage['outputTokens']} | "
                   f"prompt: {Path(args.prompt).name}_\n\n{text}\n")
    print(text)
    print(f"\n---\ntokens in={usage['inputTokens']} out={usage['outputTokens']} total={usage['totalTokens']} "
          f"| calls today={calls + 1}/{MAX_CALLS}")
    shown = out.relative_to(ROOT) if out.resolve().is_relative_to(ROOT) else out
    print(f"saved -> {shown}")


if __name__ == "__main__":
    main()
