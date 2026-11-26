## ROLE
You are a backend Python engineer at Accelya , write a lambda function that will process cancellation events and decide refunds.
Your job is to make it work: implement the refund decision engine for airline ticket
cancellation events arriving on an SQS queue.

## CONTEXT  (attach or open these files first)
- AGENTS.md - our coding rules
- docs/LOGGING_CONTRACT.md and docs/logging-contract.json - the logging standard (mandatory)
- day2/data/refund_rules.json - the business rules
- The spec in the docstring of day2/src/cancellation/handler.py
- One synthetic fixture record (paste one Record from day2/data/cancellation_events.json - synthetic data only, never real customer data):
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
    }
## EXAMPLES
Input: fare_class "J", departure_status "scheduled", no_show_flag false, fare_amount "68990.50"
Output decision: {"event_id": "evt-1004", "decision": "APPROVE", "refund_rule_id": "R-BUSINESS", "refund_amount": "68990.50"}
Input: fare_class "L", departure_status "scheduled", no_show_flag true, fare_amount "52310.00"
Output decision: {"event_id": "evt-1003", "decision": "REJECT", "refund_rule_id": "R-NOSHOW", "refund_amount": "0.00"}

## NON-GOALS
- No classes, factories or strategy patterns. Plain functions.
- Do not call SQS, S3 or any AWS API. Do not send messages anywhere.
- Do not log the message body, passenger_name, contact_email or fare_amount.
- Do not raise an unhandled exception for a bad record — isolate failures in batchItemFailures and keep processing the rest of the batch.

## OUTPUT FORMAT
Return ONE complete Python file for day2/src/cancellation/cancellation_handler.py, and the decision made for each records.
