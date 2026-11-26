# M3 Lab 2 - validation rule design (paper first)

**Learner:** `amol`   **File:** `day3/data/ticket_issue_AI_2026-11-25.csv` (synthetic)

Design every rule on paper **before** you write JSON. One row per rule. Use the five outline areas first,
then add anything else the file needs. Categories: `required` · `format` · `referential` · `business` · `late` · `duplicate`.
Routes: `quarantine` (→ `quarantine/<category>/`) · `flag` (→ `validated/` with a flag) · `skip` (not written, counted).

| Rule id | Area | Field | Check (plain English) | Category | Route | Why (one line) |
|---|---|---|---|---|---|---|
| R-01 | all | pnr, ticket_number, airline_code, fare_class, od_pair, issue_date, fare_amount, currency, agency_iata | must not be empty | required | quarantine | nothing to settle or trace without mandatory fields |
| R-02 | PNR | pnr | 6 upper-case letters/digits (regex `^[A-Z0-9]{6}$`) | format | quarantine | PNR is a 6-char alphanumeric locator; lowercase or wrong length is malformed |
| R-03 | airline code | airline_code | exactly 2 upper-case letters/digits (regex `^[A-Z0-9]{2}$`) | format | quarantine | IATA airline designator is always 2 characters |
| R-04 | airline code | airline_code | must exist in airlines.json | referential | quarantine | cannot settle against an unknown airline |
| R-05 | fare class | fare_class | exactly 1 upper-case letter (regex `^[A-Z]$`) | format | quarantine | booking class is a single letter; lowercase or multi-char is malformed |
| R-06 | fare class | fare_class | must exist in fare_classes.json | referential | quarantine | fare class must be a published booking class for this airline |
| R-07 | OD pair | od_pair | format AAA-BBB, 3-letter IATA codes (regex `^[A-Z]{3}-[A-Z]{3}$`) | format | quarantine | OD pair must be two valid 3-letter airport codes separated by hyphen |
| R-08 | OD pair | od_pair | both airports must exist in airports.json | referential | quarantine | cannot route settlement for unknown airports |
| R-09 | OD pair | od_pair | origin must differ from destination | business | quarantine | a flight from BOM to BOM is not a valid itinerary |
| R-10 | fare break | fare_amount | must be a decimal number with at most 2 decimal places | format | quarantine | fare amounts follow standard currency precision |
| R-11 | fare break | fare_amount | must be greater than 0 | business | quarantine | a zero or negative fare cannot be settled |
| R-12 | fare break | currency | must exist in currencies.json (INR, USD, AED, GBP, EUR, SGD) | referential | quarantine | cannot settle in an unrecognised currency |
| R-13 | ticket | ticket_number | format NNN-NNNNNNNNNN, 3-digit prefix + hyphen + 10 digits (regex `^\d{3}-\d{10}$`) | format | quarantine | IATA ticket number is a 3-digit airline prefix followed by 10-digit serial |
| R-14 | ticket | ticket_number | 3-digit prefix must match the airline's ticket_prefix in airlines.json | referential | quarantine | ticket prefix 098 belongs to AI; a mismatch means the ticket is not this airline's |
| R-15 | issue date | issue_date | must parse as a valid date in YYYY-MM-DD format | format | quarantine | inconsistent date formats break downstream processing |
| R-16 | issue date | issue_date | must not be after the file's business date (from filename) | business | quarantine | a ticket cannot be issued in the future relative to the file date |
| R-17 | issue date | issue_date | if more than 30 days before the file's business date, flag as late_issue | late | flag | valid but unusually old; finance should review but not reject |
| R-18 | agency | agency_iata | must be an 8-digit numeric string (regex `^\d{8}$`) | format | quarantine | IATA agency number is an 8-digit numeric code |
| R-19 | ticket | ticket_number | must not have been seen earlier in this file | duplicate | skip | first occurrence is processed; subsequent duplicates are counted, not written |
| F-01 | whole file | header | columns must be exactly: pnr,ticket_number,airline_code,fare_class,od_pair,issue_date,fare_amount,currency,agency_iata — in order | schema | quarantine whole file | if columns are renamed or reordered, no record can be trusted; reject the entire file |

## Three routing decisions to justify (one or two lines each)

1. **Late ticket** (valid, issued 56 days before the file date): **flag**, not quarantine. The ticket is real and valid — it just arrived late. Finance should see it flagged as `late_issue` in the validated zone so they can investigate the delay, but it should still be settled. Quarantining a valid ticket would delay legitimate revenue.

2. **Duplicate ticket in the same file**: **skip**, not quarantine. The first occurrence is processed normally (validated or quarantined per its own data quality). Subsequent duplicates are silently skipped and counted in the manifest as `skipped_duplicate_count`. Quarantining would create noise in the quarantine zone for something that is not a data quality issue — it is a delivery issue.

3. **Header renamed** (`currency` → `fare_currency`): **reject the whole file**. If even one column name has changed, the CSV parser maps values to wrong fields. Processing individual records would silently assign `fare_currency` values to the wrong column, corrupting downstream settlement. The entire file goes to `quarantine/schema/` and the agency is notified to resend with the correct header.

## Dates

All date rules compare against the **file's business date** extracted from the filename (e.g. `2026-11-25` from `ticket_issue_AI_2026-11-25.csv`), **never today's date**. This means a rerun next month produces the exact same validation outcomes — the same records pass, the same records are quarantined, the same records are flagged as late. Reproducibility requires that validation is deterministic relative to the file, not the clock.
