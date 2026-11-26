# M5 Lab 1 - Lambda, Fargate or Glue?  (<your-id>, pair: <neighbour-id>)

Decide with the numbers, not the fashion. Lambda: max 15 min, max 10,240 MB memory, 250 MB unzipped zip package
(10 GB as a container image). Fargate: no time limit, up to 16 vCPU / 120 GB, per-second billing (1-minute minimum).
Glue (Spark): distributed, billed per DPU-hour (per second, 1-minute minimum); default job timeout 8 h on Glue 5.0+ (48 h on 4.0 and earlier), and any timeout must be under 7 days.
Note: W2 fits Lambda today as a container image. The question is what happens as it grows, and what a standalone nightly job needs.

## The three workloads

| # | Workload | Facts |
|---|----------|-------|
| W1 | Validate each ticket-issue file as it lands (Day 4) | 40-2,000 rows per file, < 5 s, triggered per file, all day; pure Python + boto3 |
| W2 | Nightly enrichment: validated CSV -> curated Parquet (today) | 600-60,000 rows per night, 1-10 min, once a day; needs pyarrow (native, ~100 MB) |
| W3 | Audit rebuild: 18 months of BSP history re-partitioned by settlement_date, joined with refunds | 40 GB, ~300 million rows, quarterly, 1-2 h on one machine |

## Decision table (fill every cell: fits / doesn't fit / why)

| Criterion | W1 | W2 | W3 |
|-----------|----|----|----|
| Duration vs limit |  |  |  |
| Data volume and memory |  |  |  |
| Dependency weight |  |  |  |
| Concurrency / trigger shape |  |  |  |
| Cost shape (idle, per run) |  |  |  |
| Operational overhead |  |  |  |
| **Choice** |  |  |  |

## The one-line reason a reviewer would accept

- W1:
- W2:
- W3:

## What would flip W2 to a different choice?

-
