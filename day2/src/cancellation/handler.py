"""Accelya M2 - cancellation refund decision Lambda (SQS-triggered).   <-- YOU BUILD THIS IN LAB 3

Spec (give this whole docstring to your assistant, together with AGENTS.md and docs/LOGGING_CONTRACT.md):

Input   An SQS batch: event["Records"], each record has
        - messageId
        - body: JSON cancellation event (see day2/data/cancellation_events.json)
        - messageAttributes.correlation_id.stringValue  (may be missing -> mint one)

Rules   day2/data/refund_rules.json. First match wins:
        R-NOSHOW (no_show_flag) -> REJECT 0%
        R-DEPARTED (departure_status == "departed") -> REJECT 0%
        fare family FLEX / BUSINESS -> APPROVE 100%, SAVER -> APPROVE 75%
        anything else -> MANUAL_REVIEW 0% (R-UNKNOWN)
        refund_amount = fare_amount * pct / 100 with Decimal, ROUND_HALF_UP, 2 dp, as a string

Errors  A record whose body is not valid JSON, or that misses a required field
        (event_id, fare_class, departure_status, no_show_flag, fare_amount), must NOT crash the batch.
        Report it in batchItemFailures (partial batch response) and log it at ERROR with error_type.

Output  {"batchItemFailures": [{"itemIdentifier": "<messageId>"}, ...],
         "decisions": [{"event_id", "decision", "refund_rule_id", "refund_amount"}, ...]}

Logs    Follow docs/LOGGING_CONTRACT.md exactly. service = "accelya-m2-cancellation".
        Never log the body, passenger_name, contact_email or fare_amount.

Check   python3 day2/tests/run_and_check.py   (all 6 checks must PASS)
"""


def handler(event, context):
    raise NotImplementedError("Lab 3: build this handler with your assistant")
