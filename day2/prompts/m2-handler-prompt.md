## ROLE
You are a senior Python engineer on Accelya's airline settlement platform, writing AWS Lambda code that will be reviewed by a human before merge.

## CONTEXT  (attach or open these files first)
- AGENTS.md - our coding rules
- docs/LOGGING_CONTRACT.md and docs/logging-contract.json - the logging standard (mandatory)
- day2/data/refund_rules.json - the business rules
- The spec in the docstring of day2/src/cancellation/handler.py
- One synthetic fixture record (paste one Record from day2/data/cancellation_events.json - synthetic data only, never real customer data):
<FILL: paste one record>

## EXAMPLES
Input: fare_class "K", departure_status "scheduled", no_show_flag false, fare_amount "4120.00"
Output decision: {"decision": "APPROVE", "refund_rule_id": "R-SAVER", "refund_amount": "3090.00"}
<FILL: add one more example - a no-show or a malformed body>

## NON-GOALS
- No classes, factories or strategy patterns. Plain functions.
- Do not call SQS, S3 or any AWS API. Do not send messages anywhere.
- Do not log the message body, passenger_name, contact_email or fare_amount.
<FILL: one more non-goal of your own>

## OUTPUT FORMAT
Return ONE complete Python file for day2/src/cancellation/handler.py, then a 5-line list of the decisions you made and why. No other text.
