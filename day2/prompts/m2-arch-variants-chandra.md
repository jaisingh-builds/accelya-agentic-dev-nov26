## ROLE
You are a senior AWS architect for Accelya's airline settlement platform (region us-east-1).

## CONTEXT
Business flow: a passenger or agent cancels a ticket. The cancellation event (pnr, ticket_number, fare_class,
departure_status, no_show_flag, fare_amount) must get a refund decision (APPROVE / REJECT / MANUAL_REVIEW)
and the settlement team must see every decision.
Volume: 2,000 cancellations/day (~1.4/min average), peaks of 50/minute after a flight disruption.
Ordering: no PNR ordering required - use it to choose queue type consciously.

Platform constraints (non-negotiable):
- Serverless only. Max 10 Lambda functions. No provisioned database (no RDS/Aurora instances).
- Naming: every resource `accelya-m2-chandra-<name>`. IAM: named actions on ARN-scoped resources, never "*".
- Logging follows docs/LOGGING_CONTRACT.md: JSON lines, correlation_id on every record,
  identifiers and counts only.
- SQS->Lambda batch semantics: a STANDARD queue uses batchItemFailures for partial-batch retry;
  a FIFO queue requires stopping at the first failed record.
- Notifications to the settlement team may carry event_id, decision, refund_rule_id and pnr only -
  never fare_amount or passenger data.

## EXAMPLES
One event: {"event_id": "evt-1002", "pnr": "LM4K8D", "fare_class": "K", "departure_status": "scheduled",
"no_show_flag": false, "fare_amount": "4120.00"}  ->  APPROVE, R-SAVER, refund 3090.00

## NON-GOALS
- No Kinesis, no MSK, no EC2 or servers to patch.
- Never log passenger names, emails or fare amounts.
- No complex business logic, keep it simple.

## VERIFICATION REQUIREMENTS (the model must prove, not assert)
- Every hop must name a REAL AWS integration - if service A cannot invoke service B natively,
  the option is invalid (e.g. a database cannot publish to SNS).
- Check each option against every non-goal and constraint above; reject violating options in the
  comparison table, don't silently include them.
- Cost: per-day number AND its derivation - unit price x volume per service, using list prices
  you state explicitly. Sanity check: 2,000 events/day is ~$5/month total at most; a monthly
  figure above that means your arithmetic is wrong - redo it.
- Failure path must name the exact mechanism (batchItemFailures, DLQ, visibility timeout,
  retry count) not "retries".

## OUTPUT FORMAT
Exactly THREE architecture options. For each: a one-line summary, the services in order, the six
service-boundary answers for every hop, two risks, and a rough cost driver (including how you
derived the cost). End with a comparison table that includes a cost/day row and marks any option
that violates a constraint.
