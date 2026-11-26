-- Register the curated Parquet dataset in the Glue Data Catalog (explicit registration, no crawler).
-- settlement_date and airline_code are partition FOLDERS, not columns inside the files.
CREATE EXTERNAL TABLE IF NOT EXISTS ticket_issue (
  pnr            string,
  ticket_number  string,
  airline_name   string,
  fare_class     string,
  origin         string,
  destination    string,
  issue_date     date,
  fare_amount    decimal(12,2),
  currency       string,
  agency_iata    string,
  flags          string,
  business_date  date,
  source_key     string,
  source_line    int
)
PARTITIONED BY (settlement_date date, airline_code string)
STORED AS PARQUET
LOCATION 's3://${BUCKET}/curated/ticket_issue/'
