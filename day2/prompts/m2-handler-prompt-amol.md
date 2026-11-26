## ROLE
You are a Python developer helping me write a Lambda function for our airline settlement system at Accelya.

## CONTEXT
I need to build a Lambda that reads cancellation events from an SQS queue and decides if we should refund the passenger or not.

Please check these files first:
- AGENTS.md has our coding rules
- docs/LOGGING_CONTRACT.md tells how to write logs (must be JSON, one object per line)
- day2/data/refund_rules.json has the refund rules
- day2/src/cancellation/handler.py has the spec in its docstring

Here is what a cancellation event looks like:
{"event_id": "evt-1001", "pnr": "QX7P2L", "fare_class": "Y", "departure_status": "scheduled", "no_show_flag": false, "fare_amount": "8450.00", "passenger_name": "Ananya Rao", "contact_email": "ananya.rao@example.com"}

The refund logic is basically:
1. Passenger didn't show up -> no refund
2. Flight already left -> no refund
3. Expensive ticket types like FLEX or BUSINESS class -> full refund
4. Cheaper ones like ECONOMY/SAVER -> 75% refund
5. Anything we don't recognize -> send to manual review

For calculating refund amounts please use Decimal so we don't get floating point errors. Round to 2 decimal places.

If any record in the batch has bad data or is missing fields, don't let it crash everything. Just mark it as failed and log an error for it.

## EXAMPLES
Saver fare:
  fare_class "K", flight not departed, not a no-show, fare is 4120.00
  -> approve it, rule R-SAVER, refund 3090.00

No-show:
  fare_class "L", no_show is true, fare is 52310.00
  -> reject, rule R-NOSHOW, refund 0.00

## NON-GOALS
- Keep it simple, just plain functions. No need for classes or patterns.
- This Lambda only makes the decision. Don't call SQS or S3 or anything else.
- Don't put passenger names or emails or fare amounts in the logs.
- Only use standard Python libraries, nothing extra to install.

## OUTPUT FORMAT
give output in json with pnr,cancellation status,cancellation amount,cancellation date and
Give me the complete Python file for day2/src/cancellation/handler.py and tell me what choices you made.
