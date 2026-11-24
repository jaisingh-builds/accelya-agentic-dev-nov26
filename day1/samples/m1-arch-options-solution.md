## Business requirement (one line, from the business)
Every time an agency uploads a daily ticket-issue file, finance must see how many tickets arrived, per airline, within 5 minutes.

## Technical prompt

**Role:** You are designing for Accelya's settlement platform on AWS (region us-east-1).

**Input:** CSV file `ticket_issue_<airline>_<date>.csv`, ~20 MB (~100,000 rows, UTF-8, header row), columns: pnr, ticket_number, airline_code, fare_class, od_pair, issue_date, fare_amount, currency, agency_iata.

**Arrival:** One file per airline per day, about 40 airlines, mostly between 00:00 and 03:00 IST. Late or re-sent files happen a few times a week. A re-sent file has the same name and must not be double-counted.

**Must:**
- The original file is kept unchanged for 7 years for audit; processing never modifies or deletes it.
- Finance sees the row count per airline per file within 5 minutes of upload, including files that fail validation (count 0, status FAILED).
- Every step writes JSON logs with a correlation_id so one file can be traced end to end.

**Must not:**
- No servers or clusters to patch or scale.
- Never log or return fare_amount, PNR lists or ticket numbers. Counts and file names only.

**Constraints:** serverless only; sandbox limits: max 10 Lambda functions, no public buckets. The bucket is owned by the airline data team; our code reads it through a scoped IAM role, not bucket-wide credentials.

**Output wanted:** two architecture options, answered against the six service-boundary questions for every hop (push or pull, sync or async, payload, role, failure path, correlation ID). For each option, give a one-line summary, the services in order, the two biggest risks, and the main cost driver. End with a comparison table.
