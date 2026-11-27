# Day 5 labs - copyable commands

Run everything from the repo root, with the venv active. `<id>` is your learner id (e.g. `priya`).
Lab 1 is on paper. Labs 2-4 use the **standard** sandbox. Docker is optional: Lab 2 has a cloud build for laptops without it.

**Today's input is a checked-in fixture, not your Day 4 stack.** (Day 4's last slide said we'd redeploy Day 4; we don't
need to.) `day5/data/validated/` holds 21 validated files (AI, BA, EK, business dates 2026-11-19..25, 606 tickets).
`ticket_issue_AI_2026-11-25.csv` is byte-identical to what your Day 4 Lambda wrote; the other 20 were produced by the
same classifier and rules. `day5/data/remittance/` is the BSP remittance file for settlement 2026-11-27.

## Lab 0 - sync

```bash
source .venv/bin/activate                      # Windows Git Bash: source .venv/Scripts/activate
source day1/scripts/env.sh <id>                # sets L
git add day4; git commit -m "m4: $L"; git push # yesterday's work first: three steps, so the push runs even if nothing is new
git checkout main && git pull
git checkout -b day5-$L
pip install -q -r requirements.txt             # adds pyarrow 21.0.0
python3 day5/tests/run_tests.py                # job tests 9/9, Dockerfile 0/8 -> keep going
docker version --format '{{.Server.Version}}'  # a version = local Docker works. An error = use the cloud build in Lab 2
```

## Lab 1 - Lambda, Fargate or Glue? (paper, pairs)

```bash
cp day5/design/m5-selection.template.md day5/design/m5-selection-$L.md
```
Fill the decision table for the three workloads, pick one runtime each, and write the one-line reason a reviewer
would accept. Your neighbour argues for the runtime you didn't pick. Then:
```bash
git add day5/design && git commit -m "m5 lab1: $L selection" && git push -u origin day5-$L
```

## Lab 2 - Dockerfile, image, ECR (STANDARD sandbox)

```bash
export AWS_PROFILE=accelya                     # standard sandbox
aws sts get-caller-identity --query Account --output text   # must match the account shown in Pluralsight
source day5/scripts/env.sh <id>                # sets S, B, REPO, ECR, IMG, CL, TD, LG, WG, GDB

# 1. deploy the Day 5 stack (about 2 minutes): ECR repo, cluster, task definition, two roles, VPC, Glue database, Athena workgroup
(cd day5 && sam deploy --template-file template.yaml --stack-name $S $F --parameter-overrides LearnerId=$L GlueDatabase=$GDB)
source day5/scripts/env.sh <id>                # again: now SUBNET and SG are set
```

2. Write `day5/job/Dockerfile` with your assistant: prompt `day5/prompts/m5-prompts.md`, **Part A**. Then:
```bash
python3 day5/scripts/check_dockerfile.py       # Dockerfile: 8/8 checks pass
```

3a. **With Docker** - build for Fargate's x86_64 (also on an Apple-silicon Mac), check it, push:
```bash
docker build --platform linux/amd64 -t $REPO:v1 day5/job
docker run --rm --platform linux/amd64 --entrypoint python $REPO:v1 -c "import pyarrow, enrich; print('image ok, pyarrow', pyarrow.__version__)"
aws ecr get-login-password | docker login --username AWS --password-stdin $ECR
docker tag $REPO:v1 $IMG
docker push $IMG
```

3b. **Without Docker** - the same build, in CodeBuild (about 1 minute):
```bash
python3 day5/scripts/build_in_cloud.py $L      # ... build SUCCEEDED, pushed accelya-m5-<id>-enrich:v1  sha256:...
```

Either way:
```bash
aws ecr describe-images --repository-name $REPO --image-ids imageTag=v1 --query 'imageDetails[0].{tag:imageTags[0],bytes:imageSizeInBytes}'
git add day5 && git commit -m "m5 lab2: $L image v1" && git push
```
Tags are **immutable**: pushing a different image as `v1` fails. A change is `v2`: push it, then redeploy with `ImageTag=v2`.

## Lab 3 - run the task once

```bash
# 1. land today's input
aws s3 sync day5/data/validated s3://$B/validated/ --quiet
aws s3 ls s3://$B/validated/ --recursive | wc -l                      # 21

# 2. run once (correlation id d5-001) and wait for STOPPED (about 1 minute)
TASK=$(aws ecs run-task --cluster $CL --task-definition $TD --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[$SUBNET],securityGroups=[$SG],assignPublicIp=ENABLED}" \
  --overrides '{"containerOverrides":[{"name":"enrich","environment":[{"name":"CORRELATION_ID","value":"'$L'-d5-001"}]}]}' \
  --query 'tasks[0].taskArn' --output text)
echo $TASK
aws ecs wait tasks-stopped --cluster $CL --tasks $TASK
aws ecs describe-tasks --cluster $CL --tasks $TASK --query 'tasks[0].{status:lastStatus,exit:containers[0].exitCode,reason:stoppedReason}'
#   STOPPED, exit 0, "Essential container in task exited"

# 3. the logs of THIS task (its own stream, raw messages): same contract as Days 2 and 4, from a container
python3 day5/scripts/task_logs.py $TASK | python3 day2/scripts/check_logs.py -        # CONFORMANT, 30 application lines
python3 day5/scripts/task_logs.py $TASK | grep -c "$L-d5-001"                         # 30: every line carries this run's id

# 4. the output: 7 Parquet partitions and a balanced manifest
aws s3 ls s3://$B/curated/ticket_issue/ --recursive | awk '{print $4}'
aws s3 cp s3://$B/manifests/curated/ticket_issue.manifest.json - \
  | python3 -c "import json,sys; m=json.load(sys.stdin); print(m['rows_in'], m['rows_out'], 'balanced', m['balanced'], len(m['partitions']), 'partitions')"
#   606 606 balanced True 7 partitions
```

## Lab 4 - catalogue, query, reconcile

```bash
# 1. the remittance file, then register both tables (explicit DDL, no crawler) and load the partitions
aws s3 cp day5/data/remittance/bsp_remittance_2026-11-27.csv s3://$B/remittance/
python3 day5/scripts/athena.py day5/sql/01_create_curated.sql
python3 day5/scripts/athena.py day5/sql/02_load_partitions.sql
python3 day5/scripts/athena.py day5/sql/03_create_remittance.sql
python3 day5/scripts/athena.py -e "SHOW PARTITIONS ticket_issue"      # 7 partitions

# 2. what a partition filter saves: the same result on this fixture (179 tickets), filtered on columns vs on partitions. Compare "scanned="
python3 day5/scripts/athena.py day5/sql/04_no_pruning.sql             # issue_date + airline_name: every file is opened
python3 day5/scripts/athena.py day5/sql/05_pruned.sql                 # settlement_date + airline_code: one folder
```

3. With your assistant (prompt **Part B**), write `day5/sql/10_unmatched_remittance.sql` and
`day5/sql/11_settlement_recon.sql`. Run them as often as you like with `athena.py`, then:
```bash
python3 day5/scripts/check_recon.py                                   # reconciliation: 2/2 checks pass
python3 day5/scripts/athena.py day5/sql/12_recon_summary.sql          # the settlement summary
git add day5 && git commit -m "m5 lab4: $L recon 2/2" && git push
```

## Lab 5 - review, then clean up

```bash
cp day5/reviews/m5-review.template.md day5/reviews/m5-review-$L.md
```
Review a neighbour's branch: their selection table, their Dockerfile (8/8 is the floor, not the review) and their
two queries. Then, in the standard sandbox:
```bash
bash day5/scripts/cleanup_day5.sh $L           # Left-overs check prints nothing
git add day5/reviews && git commit -m "m5 lab5: $L review" && git push
```
