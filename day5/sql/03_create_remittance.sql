-- The BSP remittance file: what agencies actually paid, per ticket, on the settlement date.
CREATE EXTERNAL TABLE IF NOT EXISTS remittance (
  remittance_date  date,
  agency_iata      string,
  airline_code     string,
  ticket_number    string,
  remitted_amount  decimal(12,2),
  currency         string
)
ROW FORMAT DELIMITED FIELDS TERMINATED BY ','
STORED AS TEXTFILE
LOCATION 's3://${BUCKET}/remittance/'
TBLPROPERTIES ('skip.header.line.count'='1')
