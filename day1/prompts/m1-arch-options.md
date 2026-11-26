## Business requirement (one line, from the business)
Every time an agency uploads a daily ticket-issue file, finance must see how many tickets arrived, per airline, within 5 minutes.

## Technical prompt

**Role:** You are designing for Accelya's settlement platform on AWS (region us-east-1).

**Input:** CSV file `ticket_issue_<airline>_<date>.csv`, ~700 KB typical (10,000 rows), up to ~3.5 MB (50,000 rows) at peak, columns: pnr, ticket_number, airline_code, fare_class, od_pair, issue_date, fare_amount, currency, agency_iata.

**Arrival:** each agency uploads one file per airline per day, ~200 files per day, mostly in a burst of about an hour after the daily cut-off; every file's count must be visible within 5 minutes, even at peak.

**Must:** the original file is kept unchanged for audit, and failed files are stored separately for audit; finance sees a running ticket count per airline per day on a dashboard within 5 minutes of upload; a malformed file triggers a notification and does not block other files.

**Must not:** the finance view shows only the ticket count per airline, with no pnr, fare_amount, fare_class or od_pair; no servers to patch and no database we manage ourselves (a managed serverless one is fine), and keep the pipeline simple.

**Constraints:** serverless only; sandbox limits: max 10 Lambda functions, no public buckets.

**Output wanted:** two architecture options, answered against the six service-boundary questions for every hop.
