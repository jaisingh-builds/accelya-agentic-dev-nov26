
**Role:** You are designing for Accelya's settlement platform on AWS (region us-east-1).

**Input:** CSV file ticket_issue_EK_2026-11-23.csv, 10kb, columns: pnr, ticket_number, airline_code, fare_class, od_pair, issue_date, fare_amount, currency, agency_iata.

**Arrival:** 1 file per airline per day

**Must:** 
	1. file should not be updated
	2. file details to be shared after loading

**Must not:**
	1. do not share the details of data
	2. in case of failuer the file should stay intact

**Constraints:** serverless only; sandbox limits: max 10 Lambda functions, no public buckets.

**Output wanted:** two architecture options, answered against the six service-boundary questions for every hop.
