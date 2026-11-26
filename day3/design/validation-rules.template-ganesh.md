# M3 Lab 2 - validation rule design (paper first)

**Learner:** `<your-id>`   **File:** `day3/data/ticket_issue_AI_2026-11-25.csv` (synthetic)

Design every rule on paper **before** you write JSON. One row per rule. Use the five outline areas first,
then add anything else the file needs. Categories: `required` · `format` · `referential` · `business` · `late` · `duplicate`.
Routes: `quarantine` (→ `quarantine/<category>/`) · `flag` (→ `validated/` with a flag) · `skip` (not written, counted).

| Rule id | Area | Field | Check (plain English) | Category | Route | Why (one line) |
|---|---|---|---|---|---|---|
| R-01 | all | pnr, ticket_number, airline_code, fare_class, od_pair, issue_date, fare_amount, currency, agency_iata | must not be empty | required | quarantine | nothing to settle or trace without them |
| R-02 | PNR | pnr | exactly 6 upper-case letters/digits (regex `^[A-Z0-9]{6}$`) | format | quarantine | BSP PNR format; lowercase or wrong length cannot be matched to GDS |
| R-03 | airline code | airline_code | exactly 2 upper-case letters (regex `^[A-Z]{2}$`) | format | quarantine | IATA airline codes are always 2 letters; 3-letter codes (e.g. AIR) are not valid IATA |
| R-04 | airline code | airline_code | value exists in airlines reference list (AI, BA, EK) | referential | quarantine | unknown airline code cannot be settled; no contract on file |
| R-05 | fare class | fare_class | exactly 1 upper-case letter (regex `^[A-Z]$`) | format | quarantine | booking class is a single character; lowercase (k) is a data-entry error |
| R-06 | fare class | fare_class | value exists in fare_classes reference list | referential | quarantine | class not in airline's published inventory cannot be priced |
| R-07 | OD pair | od_pair | format `AAA-BBB` — two 3-letter IATA codes separated by a hyphen (regex `^[A-Z]{3}-[A-Z]{3}$`) | format | quarantine | missing hyphen (BOMDXB) or wrong length cannot be parsed into origin/destination |
| R-08 | OD pair | od_pair | origin and destination must be different | business | quarantine | a ticket from and to the same airport (BOM-BOM) is not a valid itinerary |
| R-09 | OD pair | od_pair | both origin and destination exist in airports reference list | referential | quarantine | unknown airport code (XYZ) cannot be routed or settled |
| R-10 | fare break | fare_amount | parseable as a decimal number with at most 2 decimal places | format | quarantine | extra decimals (4520.555) indicate a calculation error; cannot round without losing audit trail |
| R-11 | fare break | fare_amount | value must be greater than zero | business | quarantine | negative fare amount (refund/void) belongs in a separate adjustment file, not a ticket-issue file |
| R-12 | fare break | currency | value exists in currencies reference list (INR, USD, AED, GBP, EUR, SGD) | referential | quarantine | unknown currency (INX) cannot be converted or settled |
| R-13 | ticket | ticket_number | format `NNN-NNNNNNNNNN` — 3-digit airline prefix, hyphen, 10-digit serial (regex `^[0-9]{3}-[0-9]{10}$`) | format | quarantine | BSP ticket number must be exactly 13 digits plus separator; wrong length (098-75716779) cannot be matched |
| R-14 | ticket | ticket_number | 3-digit prefix must match the ticket_prefix of the row's airline_code in the airlines reference | referential | quarantine | prefix/airline mismatch (125- with AI) means the ticket was issued on a different carrier's stock |
| R-15 | issue date | issue_date | parseable as YYYY-MM-DD (ISO 8601) | format | quarantine | non-ISO format (25/11/2026) cannot be reliably parsed across locales |
| R-16 | issue date | issue_date | date is not in the future relative to the file's business date | business | quarantine | a ticket issued after the file date is impossible; indicates a date error |
| R-17 | issue date | issue_date | date is not more than 30 days before the file's business date | late | flag | late tickets are valid but need manual settlement review; flag rather than reject to preserve revenue |
| R-18 | agency | agency_iata | exactly 8 digits (regex `^[0-9]{8}$`) | format | quarantine | IATA agency codes are always 8 numeric digits; wrong format cannot be looked up in BSP agency register |
| D-01 | ticket | ticket_number | ticket_number not seen in an earlier row of this same file | duplicate | skip | same ticket appearing twice in one file is a system duplicate; count it but do not write it twice |
| F-01 | whole file | header | column names and order exactly match agreed schema | schema | quarantine whole file | a renamed column (currency → fare_currency) shifts every downstream mapping; safer to reject the whole file than silently misroute all rows |

## Three routing decisions to justify (one or two lines each)

1. **Late ticket** (valid, issued 56 days before the file date): quarantine or flag? Why?
   **Flag** (route to `validated/` with a flag). The ticket is structurally valid — it has a real airline, fare class, amount and agency. Quarantining it would cause revenue loss. Flagging it preserves the row for settlement while alerting the operations team to investigate the delay. Rule R-17 applies.

2. **Duplicate ticket in the same file**: quarantine or skip? What must the manifest show?
   **Skip** (do not write the second occurrence, but count it). Writing it twice would double-count revenue. The manifest must record `duplicate: 1` so auditors know the row was seen and consciously discarded, not silently lost.

3. **Header renamed** (`currency` → `fare_currency`): reject one record, or the whole file? Why?
   **Quarantine the whole file** (rule F-01). A header rename shifts every column mapping for every row. There is no safe way to process even the "good" rows because the parser would read `fare_currency` values into the wrong field. Rejecting early prevents silent data corruption downstream.

## Dates

Which date do your date rules compare against: today's date, or the file's business date? What happens on a rerun next month?

Rules R-16 and R-17 compare against the **file's business date** (extracted from the filename, e.g. `2026-11-25`), not today's date. If the same file is reprocessed next month, every row gets the same result — the validation is deterministic and idempotent. Using today's date would cause a row that passed on day 1 to fail on day 31, breaking reruns and making audit trails unreliable.
