## Business requirement (one line, from the business)
Every time an agency uploads a daily ticket-issue file, finance must see how many tickets arrived, per airline, within 5 minutes.

## Technical prompt

**Role:** You are designing for Accelya's settlement platform on AWS (region us-east-1).

**Input:** CSV file `ticket_issue_<airline>_<date>.csv`, ~5 MB (about 50,000 rows), columns: pnr, ticket_number, airline_code, fare_class, od_pair, issue_date, fare_amount, currency, agency_iata.

**Arrival:** one file per airline per day, about 20 files per day, mostly overnight.

**Must:** keep the original file unchanged for audit; row counts visible per airline within 5 minutes; repeat uploads of the same file are safe (idempotent).

**Must not:** no servers to patch; never log fare_amount or PNR lists.

**Constraints:** serverless only; sandbox limits: max 10 Lambda functions, no public buckets.

**Output wanted:** two architecture options, answered against the six service-boundary questions for every hop.
