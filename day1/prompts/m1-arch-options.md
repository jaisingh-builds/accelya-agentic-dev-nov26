## Business requirement (one line, from the business)
Every time an agency uploads a daily ticket-issue file, finance must see how many tickets arrived, per airline, within 5 minutes.

## Technical prompt (YOU fill the <FILL> parts, then delete this line)

**Role:** You are designing for Accelya's settlement platform on AWS (region us-east-1).

**Input:** CSV file `ticket_issue_<airline>_<date>.csv`, ~<FILL: file size> , columns: pnr, ticket_number, airline_code, fare_class, od_pair, issue_date, fare_amount, currency, agency_iata.

**Arrival:** <FILL: how often and how many files per day>

**Must:** <FILL: 2-3 hard requirements, e.g. file kept unchanged for audit, row counts visible per airline>

**Must not:** <FILL: 2 non-goals, e.g. no servers to patch, never log fare_amount or PNR lists>

**Constraints:** serverless only; sandbox limits: max 10 Lambda functions, no public buckets.

**Output wanted:** two architecture options, answered against the six service-boundary questions for every hop.
