## Business requirement (one line, from the business)
Every time an agency uploads a daily ticket-issue file, finance must see how many tickets arrived, per airline, within 5 minutes.

## Technical prompt

**Role:** You are designing for Accelya's settlement platform on AWS (region us-east-1).

**Input:** CSV file `ticket_issue_AI_2026-11-23.csv`, ~100kb , columns: pnr, ticket_number, airline_code, fare_class, od_pair, issue_date, fare_amount, currency, agency_iata.

**Arrival:** One file per agency per airline per day, ~200 files/day, arriving throughout the day with peaks after midnight UTC.

**Must:** Original file must be stored unchanged for audit and regulatory purposes.
users must see ticket counts per airline within 5 minutes of file upload.

**Must not:** No servers or operating systems requiring patching or maintenance.
Never log or expose sensitive business data such as fare_amount values, PNR lists, or ticket numbers in application logs.

**Constraints:** serverless only; sandbox limits: max 10 Lambda functions, no public buckets.

**Output wanted:** two architecture options, answered against the six service-boundary questions for every hop.
