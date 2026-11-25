## Business requirement (one line, from the business)
Every time an agency uploads a daily ticket-issue file, finance must see how many tickets arrived, per airline, within 5 minutes.

**Role:** You are designing for Accelya's settlement platform on AWS (region us-east-1).

**Input:** CSV file `ticket_issue_<airline>_<date>.csv`, ~5–20 MB (up to ~100k rows), columns: ...

**Arrival:** once per day per agency/airline pair, ~50–200 files/day, mostly between 00:00–06:00 UTC; occasional late re-uploads.

**Must:** original file kept unchanged for audit (immutable, versioned, encrypted); ticket count per airline_code visible to finance within 5 minutes of upload; duplicate/re-uploaded files must not double-count.

**Must not:** no servers or containers to patch; never log fare_amount, full rows or PNR lists — log only file identifiers and counts.

**Constraints:** serverless only; sandbox limits: max 10 Lambda functions, no public buckets.

**Output wanted:** two architecture options, answered against the six service-boundary questions for every hop.
