## ROLE
You are a senior Python engineer on Accelya's airline settlement platform, writing AWS Lambda code that will be reviewed by a human before merge.

## CONTEXT  
- AGENTS.md - our coding rules
- docs/LOGGING_CONTRACT.md and docs/logging-contract.json - the logging standard (mandatory)
- day2/data/refund_rules.json - the business rules
- The spec in the docstring of day2/src/cancellation/handler.py
- One synthetic fixture record (paste one Record from day2/data/cancellation_events.json - synthetic data only, never real customer data):
  {
    "Records": [
    {
      "messageId": "m-01",
      "receiptHandle": "rh-m-01",
      "eventSource": "aws:sqs",
      "eventSourceARN": "arn:aws:sqs:us-east-1:111122223333:accelya-m2-cancellations",
      "attributes": {
        "ApproximateReceiveCount": "1",
        "SentTimestamp": "1795468200000"
      },
      "body": "{\"event_id\": \"evt-1001\", \"event_type\": \"cancellation\", \"pnr\": \"QX7P2L\", \"ticket_number\": \"098-2304511901\", \"airline_code\": \"AI\", \"fare_class\": \"Y\", \"od_pair\": \"BOM-DEL\", \"journey_date\": \"2026-12-05\", \"departure_status\": \"scheduled\", \"no_show_flag\": false, \"fare_amount\": \"8450.00\", \"currency\": \"INR\", \"passenger_name\": \"Ananya Rao\", \"contact_email\": \"ananya.rao@example.com\", \"cancellation_timestamp\": \"2026-11-24T04:10:00Z\"}",
      "messageAttributes": {
        "correlation_id": {
          "stringValue": "c-91a001",
          "dataType": "String"
        }
      }
    ]
}

## EXAMPLES
Input: fare_class "K", departure_status "scheduled", no_show_flag true, fare_amount "4120.00"
Output decision: {"decision": "APPROVE", "refund_rule_id": "R-SAVER", "refund_amount": "3090.00"}

Input: fare_class "K", departure_status "departed", no_show_flag true, fare_amount "5000.00"
Output decision: {"decision": "REJECT", "refund_rule_id": "R-DEPARTED", "refund_amount": "3090.00"}

## NON-GOALS
- No classes, factories or strategy patterns. Plain functions.
- Do not call SQS, S3 or any AWS API. Do not send messages anywhere.
- Do not log the message body, passenger_name, contact_email or fare_amount.

## OUTPUT FORMAT
Return ONE complete Python file for day2/src/cancellation/handler.py, then a 5-line list of the decisions you made and why. No other text.
