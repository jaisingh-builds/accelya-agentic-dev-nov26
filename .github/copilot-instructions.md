# Copilot instructions (same as AGENTS.md)

Read this file before generating anything in this repository.

## Domain
- Airline settlement platform (Accelya). Use airline vocabulary only: `pnr`, `ticket_number`,
  `airline_code`, `fare_class`, `od_pair`, `settlement_date`, `agency_iata`, `fare_amount`.
- Never use generic names: `order`, `order_id`, `user`, `user_id`, `item`, `transaction_amount`.
- Glossary: `glossary/airline.md`.

## Platform
- AWS, region `us-east-1` only (Pluralsight sandbox allows us-east-1 and us-west-2). Python 3.12. Infrastructure as code with AWS SAM - no console click-ops.
- Bedrock via `bedrock-runtime` Converse API with an explicit model id.
- Sandbox limits: max 10 Lambda functions for the whole course, memory <= 2048 MB, no public S3 buckets,
  max 50 Bedrock calls / 20,000 tokens per learner.

## Coding rules
- Every resource name starts `accelya-m<module>-<learner>-`.
- IAM: named actions on ARN-scoped resources. Never `"Action": "*"` or `"Resource": "*"`.
- No secrets, keys or account ids in code. Config comes from environment variables.
- Logging follows docs/LOGGING_CONTRACT.md (machine check: python3 day2/scripts/check_logs.py). Required keys on
  every line: ts, level, service, correlation_id, boundary, message. Only keys listed in docs/logging-contract.json.
- Never log payloads, file rows, passenger names, emails, fare amounts or raw exception messages - identifiers and counts only.
- Every error path is explicit: catch the specific exception, log it at ERROR with boundary and error_type.
- Money is Decimal with ROUND_HALF_UP, never float.
- No speculative abstraction: plain functions unless there are two real implementations.

## Before you write code
Answer the six service-boundary questions (`docs/service-boundary-6q.md`) for every hop you add.

## Review
Nothing is merged without a human review using `docs/HITL_REVIEW_TEMPLATE.md`.
