## Business requirement (one line, from the business)
Every time an agency uploads a daily ticket-issue file, finance must see how many tickets arrived, per airline, within 5 minutes.

## Technical prompt

**Role:** You are designing for Accelya's settlement platform on AWS (region us-east-1).

**Input:** CSV file `ticket_issue_<airline>_<date>.csv`, 5kb , columns: pnr, ticket_number, airline_code, fare_class, od_pair, issue_date, fare_amount, currency, agency_iata.

**Arrival:** 10 file per airline per agency per day, about 50 files a day, mostly between 00:00 and 02:00 UTC.

**Must:** keep the raw file unchanged for audit; show the ticket count per airline_code within 2 minutes of upload; process a re-uploaded file only once.

**Must not:** no servers to patch; never log fare_amount or PNR lists or any PII Details.

**Constraints:** serverless only; sandbox limits: max 10 Lambda functions, no public buckets.

**Output wanted:** two architecture options, answered against the six service-boundary questions for every hop.
