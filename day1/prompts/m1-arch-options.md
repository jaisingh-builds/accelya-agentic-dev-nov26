## Business requirement (one line, from the business)
Every time an agency uploads a daily ticket-issue file, finance must see how many tickets arrived, per airline, within 5 minutes.

**Role:** You are designing for Accelya's settlement platform on AWS (region us-east-1).

**Input:** CSV file `ticket_issue_<airline>_<date>.csv`, file will always be maximum of size 100MB, columns: pnr, ticket_number, airline_code, fare_class, od_pair, issue_date, fare_amount, currency, agency_iata.

**Arrival:** files most offten will arrive at the end of each hour for each airline, but could arrive at any time.

**Must:** while file is being processed, it should not be modified or deleted.it must check if all mentioned colmns are present in the file. file must be processed within 5 minutes.

**Must not:** passenger details should not be logged or stored.

**Constraints:** serverless only; sandbox limits: max 10 Lambda functions, no public buckets.

**Output wanted:** two architecture options, answered against the six service-boundary questions for every hop.
