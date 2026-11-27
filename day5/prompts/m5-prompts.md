# M5 prompts (Copilot Chat or Devin)

Attach as context first (Copilot: `#file`; Devin: mention the paths):
`AGENTS.md`, `day5/job/enrich.py`, `day5/job/requirements.txt`, `day5/job/.dockerignore`, `day5/scripts/check_dockerfile.py`.

---

## Part A - the Dockerfile (Lab 2)

**Role.** You are a platform engineer who ships batch jobs to AWS ECS Fargate for an airline settlement team.

**Task.** Write `day5/job/Dockerfile` for the enrichment job in `enrich.py`. Return the complete file only.

**Context.**
- The build context is `day5/job/`. `.dockerignore` already limits it to `enrich.py`, `requirements.txt` and `ref/*.json`.
- The image runs on Fargate **x86_64**; we build it with `docker build --platform linux/amd64`.
- The job reads all configuration from environment variables set in the ECS task definition (see the docstring).

**Constraints.**
- Base image: Python 3.12 **slim** from ECR Public (`public.ecr.aws/docker/library/python`), pinned to an exact
  patch version (x.y.z). Never `latest`. Docker Hub throttles shared build machines.
- Copy and install `requirements.txt` **before** copying the code, with `pip install --no-cache-dir`.
- Copy only `enrich.py` and `ref/`. No `COPY . .`.
- Run as a non-root user. `ENTRYPOINT` in exec (JSON array) form.
- No secrets, keys or account ids anywhere in the file; no `ENV` for BUCKET or LEARNER_ID either (the task definition sets them).

**Non-goals.** No multi-stage build, no HEALTHCHECK (it's a run-to-completion job, not a service), no docker-compose.

**Output format.** The Dockerfile, then three lines: which layer the cache saves on a code-only change, which user
the process runs as, and how `CORRELATION_ID` reaches the job.

---

## Part B - the reconciliation queries (Lab 4)

Attach instead: `day5/sql/01_create_curated.sql`, `day5/sql/03_create_remittance.sql`,
`day5/sql/10_unmatched_remittance.sql`, `day5/sql/11_settlement_recon.sql`.

**Role.** You are an analytics engineer for BSP settlement. You write Athena (Trino) SQL that finance signs off.

**Task.** Replace the placeholder `SELECT` in `10_unmatched_remittance.sql` and in `11_settlement_recon.sql`.
Each file's header comment is the specification: output columns, their order, the status rules and their order.

**Constraints.**
- Athena engine v3 SQL. Tables `ticket_issue` (Parquet, partitioned by settlement_date, airline_code) and `remittance`.
- Money is `decimal(12,2)` on both sides: compare it directly, never through `double` or `round()`.
- Join on `ticket_number` only. Don't hard-code the settlement date in the view: derive the cut-off from `remittance`.
- One statement per file, no trailing semicolon.

**Output format.** The two complete SQL files, then for each status one sentence: which row in the data proves it.
