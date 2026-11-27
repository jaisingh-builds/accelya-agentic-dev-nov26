# Day 4 labs - copyable commands

Run everything from the repo root, with the venv active. `<id>` is your learner id (e.g. `priya`).
Labs 0-3 are local (no AWS). Lab 4 deploys to the **standard** sandbox.

## Lab 0 - sync

```bash
source .venv/bin/activate                      # Windows Git Bash: source .venv/Scripts/activate
source day1/scripts/env.sh <id>                # sets L
git add day3; git commit -m "m3: $L"; git push # yesterday's work first: three steps, so the push runs even if nothing is new
git checkout main && git pull
git checkout -b day4-$L
pip install -q -r requirements.txt             # adds aws-lambda-powertools 3.41.0 (same as the Lambda layer)
python3 day4/tests/run_tests.py                # 0/23 to start: that's the point
git show day3-$L:day3/rules/validation_rules.json | head -3   # your Day 3 rules live on your day3 branch, not on main
```

## Lab 1 - standards and your rules (local)

```bash
# your rules file is committed on day3-$L; main (and so day4-$L) doesn't have it. Package it from there:
R=day4/src/processor/rules/validation_rules.json
git show day3-$L:day3/rules/validation_rules.json > $R
#   no local day3 branch (new laptop)?  git fetch origin && git show origin/day3-$L:day3/rules/validation_rules.json > $R
python3 day3/scripts/classify.py day3/data/ticket_issue_AI_2026-11-25.csv --rules $R --check | tail -2   # 42/42 on the packaged copy
python3 day4/scripts/check_structure.py        # structure: 13/13 checks pass
git add day4/src/processor/rules && git commit -m "m4 lab1: $L rules" && git push -u origin day4-$L
```

Plant one violation, see it caught, then remove it:
```bash
echo 'BUCKET = "accelya-m4-demo-123456789012"' >> day4/src/processor/config.py
python3 day4/scripts/check_structure.py | grep FAIL   # hard-coded account id ... hard-coded resource name
git checkout day4/src/processor/config.py
```

## Lab 2 - the service (local, with your AI assistant)

Prompt: `day4/prompts/m4-processor-prompt.md`, **Part A**. Paste the generated `service.py`, then:
```bash
python3 day4/tests/run_tests.py                # unit 13/13 and functional 4/4 (logging stays 0/6 until Lab 3)
git add day4 && git commit -m "m4 lab2: $L 17/23, <k> loops" && git push
```
`functional` proves two things from the outputs alone: 42/42 routed as Day 3 expected, and the zone files are
**byte-identical** to what `classify.py --zones` wrote on Day 3.

## Lab 3 - Powertools Logger in the handler (local)

Prompt Part B. Paste the generated `handler.py`, then:
```bash
python3 day4/tests/run_tests.py                # 23/23 -> READY TO DEPLOY
git add day4 && git commit -m "m4 lab3: $L handler 23/23" && git push
```

## Lab 4 - deploy, run, prove (STANDARD sandbox)

```bash
export AWS_PROFILE=accelya                     # standard sandbox
aws sts get-caller-identity --query Account --output text   # must match the account shown in Pluralsight
source day4/scripts/env.sh <id>                # sets S, B, FN, LG, Q

# 1. deploy (about 2 minutes)
(cd day4 && sam build && sam deploy --stack-name $S $F --parameter-overrides LearnerId=$L)

# 2. land the 42-record file (correlation id d4-001), then prove the result from S3
aws s3 cp day3/data/ticket_issue_AI_2026-11-25.csv s3://$B/raw/ --metadata correlation-id=$L-d4-001
sleep 20; aws s3 ls s3://$B/ --recursive | awk '{print $4}'
aws s3 cp s3://$B/manifests/business_date=2026-11-25/ticket_issue_AI_2026-11-25.csv.manifest.json - \
  | python3 -c "import json,sys; m=json.load(sys.stdin); print(m['rows_in'], m['validated'], m['flagged'], m['duplicates'], m['quarantined'], 'balanced', m['balanced'])"

# 3. the logs: contract and correlation_id
aws logs tail $LG --since 15m --format short | python3 day2/scripts/check_logs.py -
aws logs tail $LG --since 15m --format short | grep "$L-d4-001" | grep -c '"message"'

# 4. same file again (d4-002): a no-op
aws s3 cp day3/data/ticket_issue_AI_2026-11-25.csv s3://$B/raw/ --metadata correlation-id=$L-d4-002
sleep 20; aws logs tail $LG --since 15m --format short | grep "$L-d4-002" | grep -o '"message":"[a-z_.]*"'


# 5. no metadata + wrong header: whole file to quarantine/schema, a minted id and a WARNING
aws s3 cp day3/data/ticket_issue_AI_2026-11-25_resend.csv s3://$B/raw/
sleep 20; aws logs tail $LG --since 15m --format short | grep -E 'metadata.missing|file.quarantined' | grep -o '"correlation_id":"[^"]*","boundary":"[^"]*","message":"[a-z_.]*"'

# 6. retention: deliberate, not "Never Expire"
aws logs describe-log-groups --log-group-name-prefix $LG --query 'logGroups[0].retentionInDays'   # null = forever
#    edit day4/template.yaml: under ProcessorLogGroup Properties add   RetentionInDays: 14
(cd day4 && sam build && sam deploy --stack-name $S $F --parameter-overrides LearnerId=$L)
aws logs describe-log-groups --log-group-name-prefix $LG --query 'logGroups[0].retentionInDays'   # 14

# 7. clean up, then push
bash day4/scripts/cleanup_day4.sh $L
git add day4 && git commit -m "m4 lab4: $L deployed, retention 14" && git push
```

## Lab 5 - peer review

```bash
cp day4/reviews/m4-review.template.md day4/reviews/m4-review-$L.md
```
Review a neighbour's branch with `docs/HITL_REVIEW_TEMPLATE.md`. Logging-contract conformance is a mandatory line.
