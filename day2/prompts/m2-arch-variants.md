## ROLE
You are a senior AWS architect for Accelya's airline settlement platform (region us-east-1).

## CONTEXT
Business flow: a passenger or agent cancels a ticket. Each cancellation event must get a refund decision
(APPROVE / REJECT / MANUAL_REVIEW) and the settlement team must see every decision within 5 minutes.
Source: the booking system's cancellation service sends each event to the queue using its own IAM role.
Event fields: event_id, event_type, pnr, ticket_number, airline_code, fare_class, departure_status,
no_show_flag, fare_amount, currency, cancellation_timestamp.
Volume: about 2,000 cancellations/day, normally under 5/minute, peaks of 50/minute for about an hour.
Each event is under 2 KB.
Ordering: order matters only per PNR; across PNRs it does not. Messages can be delivered more than once,
so a decision must be safe to repeat. One refund decision per ticket_number, plus event_id dedupe.

## SERVICES (use all eight, each with this job, and no other services except IAM and encryption)
- SQS FIFO: ingest queue (message group = pnr, deduplication id = event_id) and a FIFO dead-letter queue.
- Lambda: decide the refund as a pure function of the event and refund_rules.json; one archiver function.
- DynamoDB: decisions table, written with a conditional put (duplicate = already decided, not an error).
- EventBridge: custom bus carrying a "decided" event (identifiers only) from the decisions table, plus a scheduler
  that starts the Glue job.
- SNS: notifies the settlement team of every MANUAL_REVIEW decision, and carries CloudWatch alarm messages.
- S3: immutable audit archive of decisions, partitioned by airline_code and date. Private, encrypted.
- Glue: crawler and ETL job that turn the S3 archive into partitioned Parquet and a catalog for reporting.
- CloudWatch: logs, metrics and alarms.
- For each service, state the requirement it meets. If a service meets none, say so and drop it.

## RULES (from AGENTS.md)
- Serverless only; max 10 Lambda functions for the whole course (state "Lambda functions used: n of 10"), memory <= 2048 MB,
  no public S3 buckets; SAM only, Python 3.12; no console steps (list any unavoidable manual step, e.g. SNS email confirmation).
- Name every resource accelya-m2-<learner>-<purpose>. Glue and Athena names use underscores: accelya_m2_<learner>_<purpose>.
  Config comes from environment variables; no keys or account IDs.
- IAM: for every role, list the named actions and the resource ARN. Never "*".
- Logging follows docs/LOGGING_CONTRACT.md: keys ts, level, service, correlation_id, boundary, message, and only keys in
  logging-contract.json. Every failure path catches a specific exception and logs ERROR with error_type.
  Glue job and Pipes logs follow the contract too. No custom (EMF) metrics until the contract is changed by PR.
- Never log payloads, file rows, passenger names, emails, fare amounts or raw exception messages.
- passenger_name and contact_email must never enter the queue, DLQ, table, bus, bucket, logs, SNS messages, alarm text,
  the Glue catalog or Athena results. SNS messages carry event_id and pnr only.
- Money: Decimal, ROUND_HALF_UP, two decimals, string, with currency. In S3, Glue and Athena amounts are decimal(18,2)
  or string, never double; set the Glue schema explicitly.
- Plain functions; use airline vocabulary from the glossary only. Never use order, user, item or transaction_amount.

## DESIGN REQUIREMENTS
- No dual write: the "decided" event must come from the DynamoDB stream (EventBridge Pipes), or show how a failed publish is retried.
- EventBridge cannot write to S3 directly: show the hop that gets decisions into S3.
- If an event fails, say what happens to later events for the same PNR.
- Settlement reads current decisions from DynamoDB (within 5 minutes) and history through the Glue catalog.

## EXAMPLES
Input: fare_class "K", departure_status "scheduled", no_show_flag false, fare_amount "4120.00", currency "INR"
Output: {"decision": "APPROVE", "refund_rule_id": "R-SAVER", "refund_amount": "3090.00", "currency": "INR"}

Input: fare_class "L", departure_status "scheduled", no_show_flag true, fare_amount "52310.00", currency "INR"
Output: {"decision": "REJECT", "refund_rule_id": "R-NOSHOW", "refund_amount": "0.00", "currency": "INR"}

## NON-GOALS
- No Kinesis, no MSK, no EC2 or servers to patch.
- Never lose an event: a failed event ends in the dead-letter queue for review, never dropped silently.
- Every EventBridge target, Pipe and SNS subscription has a retry policy and a DLQ, so a delivery failure is never silent either.

## OUTPUT FORMAT
Keep the whole answer under 2,500 words.
1. A text flow diagram of the one architecture.
2. Compact tables, one row per hop, answering the six service-boundary questions (docs/service-boundary-6q.md),
   hop names as source->target. Cover at least: producer->sqs, sqs->lambda, lambda->dynamodb, dynamodb->pipes,
   pipes->eventbridge, eventbridge->sns, eventbridge->lambda, lambda->s3, scheduler->glue, glue->s3, glue->catalog,
   sqs->dlq, cloudwatch->sns, settlement->dynamodb, settlement->athena.
3. IAM per role: named actions and ARNs.
4. Observability: log keys used, built-in CloudWatch metrics and alarms (DLQ depth, oldest message age, Lambda errors,
   duplicates); do not use PutMetricData.
5. Four risks.
6. Cost per month as quantity x unit price = $, with assumptions, and what changes at 10x volume.
7. Score 0-3 on correctness, security, observability, scalability and cost (pass: 12/15, each at least 2), one line of evidence each.
8. End with what to drop first if cost or the sandbox rules require it.
