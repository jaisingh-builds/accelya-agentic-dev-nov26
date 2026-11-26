# M3 Lab 2 - validation rule design (paper first)

**Learner:** `rohitb`   **File:** `day3/data/ticket_issue_AI_2026-11-25.csv` (synthetic)

Design every rule on paper **before** you write JSON. One row per rule. Use the five outline areas first,
then add anything else the file needs. Categories: `required` · `format` · `referential` · `business` · `late` · `duplicate`.
Routes: `quarantine` (→ `quarantine/<category>/`) · `flag` (→ `validated/` with a flag) · `skip` (not written, counted).

Rule ids follow the phase order: required (01), format (02-09), referential (10-14), business (15-17), late (18), duplicate (19).

| Rule id | Area | Field | Check (plain English) | Category | Route | Why (one line) |
|---|---|---|---|---|---|---|
| R-01 | all | pnr, ticket_number, airline_code, fare_class, od_pair, issue_date, fare_amount, currency, agency_iata | must not be empty | required | quarantine | nothing to settle or trace without them |
| R-02 | PNR | pnr | 6 upper-case letters/digits | format | quarantine | a lower-case or short PNR won't match the booking in the reservation system |
| R-03 | airline code | airline_code | exactly 2 characters, upper-case letters or digits (e.g. `AI`, `6E`) | format | quarantine | IATA airline designators are 2 characters; `AIR` is not one |
| R-10 | airline code | airline_code | in the airlines reference list | referential | quarantine | we only settle airlines we have a contract for |
| R-04 | fare class | fare_class | exactly 1 upper-case letter | format | quarantine | a booking class is one letter; `k` must not be guessed into `K` |
| R-11 | fare class | fare_class | in the airline's published fare-class list | referential | quarantine | an unpublished class has no fare rules to price or refund against |
| R-05 | OD pair | od_pair | `AAA-BBB`: two 3-letter airport codes joined by a hyphen | format | quarantine | without the hyphen we can't split origin and destination |
| R-12 | OD pair | od_pair | both airports in the airports reference list | referential | quarantine | an unknown airport (`XYZ`) means the route can't be priced or prorated |
| R-15 | OD pair | od_pair | origin ≠ destination | business | quarantine | `BOM-BOM` is well-formed but no real journey flies to itself |
| R-06 | fare break | fare_amount | a number with at most 2 decimal places | format | quarantine | money is settled to the paisa; a third decimal means bad source data, not rounding |
| R-17 | fare break | fare_amount | greater than 0 | business | quarantine | a negative fare on an issue file is a refund or error, not a sale |
| R-13 | fare break | currency | in the accepted ISO 4217 list | referential | quarantine | `INX` can't be converted or settled |
| R-07 | ticket | ticket_number | 3-digit prefix, hyphen, 10 digits (`098-2015079145`) | format | quarantine | a short number is not a real ticket and can't be matched in BSP |
| R-14 | ticket | ticket_number | the 3-digit prefix belongs to the row's airline (`AI` → `098`) | referential | quarantine | a `125` (BA) ticket on an AI file would be settled to the wrong carrier |
| R-08 | issue date | issue_date | ISO date `YYYY-MM-DD` | format | quarantine | `25/11/2026` is ambiguous between day-first and month-first |
| R-16 | issue date | issue_date | not after the file's business date | business | quarantine | a ticket can't be issued after the file that reports it |
| R-18 | issue date | issue_date | no more than 30 days before the file's business date | late | flag | the sale is real, only reported late: settle it, but make the delay visible |
| R-09 | agency | agency_iata | exactly 8 digits | format | quarantine | a 7-digit IATA number won't match an accredited agency, so no commission or ADM can be raised |
| R-19 | ticket | ticket_number | seen earlier in this file | duplicate | skip | the first copy is already validated; count the repeat, don't settle it twice |
| F-01 | whole file | header | columns exactly as agreed | schema | quarantine whole file | a renamed column breaks every rule after it, for every row |

## Three routing decisions to justify (one or two lines each)

1. **Late ticket** (valid, issued 56 days before the file date): **flag**. The ticket is real, correctly formed revenue; quarantining it would hold back money the airline is owed, while a flag settles it and still shows the late reporting for follow-up with the agency.
2. **Duplicate ticket in the same file**: **skip**. The first copy is already in `validated/`, so there is nothing to fix and nothing for ops to review; the manifest must show `duplicates_skipped: 1` with the ticket number and line, so `42 = validated + flagged + quarantined + skipped` still balances.
3. **Header renamed** (`currency` → `fare_currency`): **reject the whole file**. The header is the contract: with `currency` missing, all 42 rows would fail with a misleading per-row reason, so quarantine the file once as `schema` and ask the sender to resend.

## Dates

All date rules compare against the **file's business date** (`2026-11-25`, taken from the file name), never today's date.
A rerun next month gives exactly the same routing; with today's date, every row would turn "late" on a rerun and the result would depend on when we ran it.
