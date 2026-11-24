# Day 3 labs - copyable commands

Run everything from the repo root, with the venv active. `<id>` is your learner id (e.g. `priya`).
Every check filters by **its own correlation id**, so earlier uploads can't be counted by mistake.

## Lab 0 - sync

```bash
source .venv/bin/activate                      # Windows Git Bash: source .venv/Scripts/activate
pip install -q -r requirements.txt
source day1/scripts/env.sh <id>                # sets L, B, LG
git add day2 && git commit -m "m2: $L" && git push   # yesterday's work first ("nothing to commit" is fine)
git checkout main && git pull
git checkout -b day3-$L
python3 day3/tests/test_classify.py            # classify self-test: OK (23 checks)
```

## Lab 1 - raw zone and S3 trigger (STANDARD sandbox)

```bash
# 1. point at the STANDARD sandbox and prove it
aws configure --profile accelya                # standard sandbox keys, us-east-1
export AWS_PROFILE=accelya
aws sts get-caller-identity --query Account --output text   # must match the account shown in Pluralsight
source day1/scripts/env.sh $L                  # re-derives B for this account

# 2. deploy Day 1's stack (about 2 minutes)
(cd day1 && sam build && sam deploy --stack-name accelya-m1-$L $F --parameter-overrides LearnerId=$L)
cp day3/reviews/m3-lab1.template.md day3/reviews/m3-lab1-$L.md
K=raw/ticket_issue_AI_2026-11-25.csv

# 3. land the file (correlation id d3-001)
aws s3 cp day3/data/ticket_issue_AI_2026-11-25.csv s3://$B/raw/ --metadata correlation-id=$L-d3-001
aws s3api head-object --bucket $B --key $K --query ETag --output text      # ETag 1 - write it down
sleep 20; aws logs tail $LG --since 15m --format short | grep "$L-d3-001" | grep -c file.received   # 1

# 4. same file to the bucket ROOT (d3-002): the prefix filter
aws s3 cp day3/data/ticket_issue_AI_2026-11-25.csv s3://$B/ --metadata correlation-id=$L-d3-002
sleep 20; aws logs tail $LG --since 15m --format short | grep -c "$L-d3-002"                       # 0

# 5. DUPLICATE DELIVERY: the same event, delivered twice (one upload, two invocations)
python3 day3/scripts/s3_event.py --bucket $B --key $K > day3/outputs/s3_event.json
aws lambda invoke --function-name accelya-m1-$L-ingest --invocation-type Event \
    --cli-binary-format raw-in-base64-out --payload file://day3/outputs/s3_event.json /dev/null   # StatusCode 202
sleep 20; aws logs tail $LG --since 15m --format short | grep "$L-d3-001" | grep -c file.received   # 2

# 6. RESEND: a new upload of the same content (d3-003) - a NEW event
aws s3 cp day3/data/ticket_issue_AI_2026-11-25.csv s3://$B/raw/ --metadata correlation-id=$L-d3-003
aws s3api head-object --bucket $B --key $K --query ETag --output text      # ETag 2 - compare with ETag 1
sleep 20; aws logs tail $LG --since 15m --format short | grep "$L-d3-003" | grep -c file.received   # 1

# 7. clean up BEFORE switching sandboxes, then push
bash day1/scripts/cleanup_day1.sh $L
git add day3/reviews && git commit -m "m3 lab1: $L" && git push -u origin day3-$L
```

Same ETag for 1 and 2 is what we observe **for this fixture** (same bytes, single-part upload, SSE-S3).
Don't rely on ETag = MD5 in general: multipart and SSE-KMS uploads differ. A hash you compute is the safe key.

## Lab 2 - rules on paper (local)

```bash
cp day3/design/validation-rules.template.md day3/design/validation-rules-$L.md
cat day3/data/ref/*.json
git add day3/design && git commit -m "m3 lab2: $L" && git push
```

## Lab 3 - rules as data (local)

```bash
cp day3/rules/validation_rules.template.json day3/rules/validation_rules.json
python3 day3/scripts/classify.py day3/data/ticket_issue_AI_2026-11-25.csv --check          # 27/42 to start
python3 day3/scripts/classify.py day3/data/ticket_issue_AI_2026-11-25_resend.csv --check   # whole file -> schema
python3 day3/scripts/classify.py day3/data/ticket_issue_AI_2026-11-25.csv --zones day3/outputs/zones --cid $L-d3-001
cat day3/outputs/zones/manifests/*/ticket_issue_AI_2026-11-25.csv.manifest.json
git add day3/rules && git commit -m "m3 lab3: $L 42/42, <n> rules, <k> loops" && git push
```

Expected at 42/42: `validated/` holds **22 rows** (21 clean + 1 flagged), quarantine 19, 1 duplicate skipped:
**42 = 21 clean + 1 flagged + 19 quarantined + 1 duplicate skipped**.

## Lab 4 - BRD (AI sandbox)

```bash
aws configure --profile accelya-ai
aws configure set aws_session_token <TOKEN> --profile accelya-ai          # only if the sandbox shows one
aws sts get-caller-identity --profile accelya-ai --query Account --output text   # must match the AI sandbox
python3 day3/scripts/draft_brd.py --learner $L --profile accelya-ai        # draft 1; prints profile + account
cp day3/reviews/m3-brd-review.template.md day3/reviews/m3-brd-review-$L.md
# paste your revision notes under ## REVISION in day3/prompts/m3-brd-prompt.md, then:
python3 day3/scripts/draft_brd.py --learner $L --profile accelya-ai --draft 2
git add day3/prompts day3/outputs day3/reviews && git commit -m "m3 lab4: $L BRD" && git push
```

No Bedrock? Use the two real rehearsal drafts: `day3/samples/m3-brd-sample-v1.md` (review it, write notes) and
`day3/samples/m3-brd-sample-v2.md` (the draft those notes produced: check each note, finish it by hand, re-score).
