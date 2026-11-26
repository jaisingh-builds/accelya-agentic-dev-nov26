# M3 Lab 2 - validation rule design (paper first)

**Learner:** `hassan`   **File:** `day3/data/ticket_issue_AI_2026-11-25.csv` (synthetic)

Design every rule on paper **before** you write JSON. One row per rule. Use the five outline areas first,
then add anything else the file needs. Categories: `required` · `format` · `referential` · `business` · `late` · `duplicate`.
Routes: `quarantine` (→ `quarantine/<category>/`) · `flag` (→ `validated/` with a flag) · `skip` (not written, counted).

Reference data: `day3/data/ref/airlines.json`, `airports.json`, `currencies.json`, `fare_classes.json`.
Rules run in phase order (required → format → referential → business → late → duplicate); the first failing
quarantine rule decides the category. "Lines" are file line numbers (line 1 = header).

| Rule id | Area | Field | Check (plain English) | Category | Route | Why (one line) | Lines it catches |
|---|---|---|---|---|---|---|---|
| R-01 | all | pnr, ticket_number, airline_code, fare_class, od_pair, issue_date, fare_amount, currency, agency_iata | must not be empty | required | quarantine | nothing to settle or trace without them; every column feeds a later rule | 8, 24 |
| R-02 | PNR | pnr | 6 upper-case letters/digits (`^[A-Z0-9]{6}$`) | format | quarantine | a PNR of the wrong length or case can't be matched to the booking | 10, 31 |
| R-03 | airline code | airline_code | exactly 2 upper-case letters/digits (`^[A-Z0-9]{2}$`; digits allowed, e.g. `6E`) | format | quarantine | IATA designators are 2 characters; `AIR` is a typo, not a carrier | 3 |
| R-04 | airline code | airline_code | in `airlines.json` | referential | quarantine | we only settle carriers we have a contract and ticket prefix for | 15 |
| R-05 | fare class | fare_class | exactly 1 upper-case letter (`^[A-Z]$`) | format | quarantine | lower-case `k` is not an RBD; never silently upper-case money-driving fields | 28 |
| R-06 | fare class | fare_class | in `fare_classes.json` (classes AI publishes) | referential | quarantine | an unpublished class has no fare rules, so no refund or commission basis | 16 |
| R-07 | OD pair | od_pair | `AAA-BBB`: two 3-letter codes joined by a hyphen (`^[A-Z]{3}-[A-Z]{3}$`) | format | quarantine | `BOMDXB` can't be split reliably into origin and destination | 32 |
| R-08 | OD pair | od_pair | both airports in `airports.json` | referential | quarantine | an unknown airport (`XYZ`) can't be priced or reported by route | 14 |
| R-09 | OD pair | od_pair | origin is not the same as destination | business | quarantine | `BOM-BOM` is well-formed but not a journey | 23 |
| R-10 | fare break | fare_amount | a number with at most 2 decimal places | format | quarantine | INR is 2 dp; `4520.555` means upstream rounding is broken, and we never round for them | 33 |
| R-11 | fare break | fare_amount | greater than 0 | business | quarantine | a ticket-issue file can't carry a negative or zero fare; refunds come through a different feed | 26 |
| R-12 | fare break | currency | in `currencies.json` (ISO 4217 codes we accept) | referential | quarantine | `INX` is not a currency; a wrong code silently mis-converts the settlement | 41 |
| R-13 | ticket | ticket_number | `NNN-NNNNNNNNNN`: 3-digit prefix, hyphen, 10 digits (`^\d{3}-\d{10}$`) | format | quarantine | a short ticket number can't be matched to BSP sales | 21 |
| R-14 | ticket | ticket_number | the 3-digit prefix is the `ticket_prefix` of that row's `airline_code` | referential | quarantine | a `125` (BA) ticket in an AI row is settled against the wrong airline | 29 |
| R-15 | issue date | issue_date | ISO date `YYYY-MM-DD` | format | quarantine | `25/11/2026` is ambiguous between formats; we don't guess dates | 4 |
| R-16 | issue date | issue_date | on or before the file's business date (from the file name) | business | quarantine | a ticket can't be issued after the file that reports it was cut | 18 |
| R-17 | issue date | issue_date | no more than 30 days before the file's business date | late | flag | valid sale reported late: settle it, but make ops look at it | 20 |
| R-18 | agency | agency_iata | exactly 8 digits (`^\d{8}$`) | format | quarantine | a 7-digit agency code can't be billed; agency is who owes the remittance | 6 |
| R-19 | ticket | ticket_number | seen earlier in this file (in an accepted record) | duplicate | skip | keep the first, don't settle the same ticket twice; count the repeat | 22 |
| R-20 | fare break | currency | exactly 3 upper-case letters (`^[A-Z]{3}$`) | format | quarantine | custom: tells "wrong shape" (`inr`, `RUPEE`) apart from "unknown code" (R-12) in the quarantine folders | none in this file |
| F-01 | whole file | header | columns exactly `pnr,ticket_number,airline_code,fare_class,od_pair,issue_date,fare_amount,currency,agency_iata`, in that order | schema | quarantine whole file | a renamed or missing column breaks every rule after it | resend file (`fare_currency`) |

**Expected result for this file:** the whole file is accepted, because F-01 passes. Of the 42 records, 19 are quarantined, 1 is flagged (line 20), 1 is skipped as a duplicate (line 22), and 21 go to `validated/` unflagged.
That is 22 records in `validated/` in total, counting the flagged one (19 + 1 + 1 + 21 = 42).
A dry run of these rules against `day3/data/expected_routing.json` routed all 42 records as expected.

## Three routing decisions to justify (one or two lines each)

1. **Late ticket** (valid, issued 56 days before the file date): **flag**. The sale is real and well-formed, so holding it back would under-remit the agency's debt. The flag in `validated/` makes ops check why it came in late, for example a missed earlier file, without blocking settlement.
2. **Duplicate ticket in the same file**: **skip**. The copy is identical to line 2, so quarantining it would only create work. The manifest must show `skipped_duplicate: 1` with the line number, the `ticket_number` and the earlier line it repeats (line 2), so that `in = validated + flagged + quarantined + skipped` adds up. If a repeat ever has *different* values, for example a different `fare_amount`, it is a conflict, not a duplicate, and should be quarantined. That needs a later rule.
3. **Header renamed** (`currency` → `fare_currency`): **reject the whole file**. Every row is affected, not one, and R-01, R-12 and R-20 would quarantine all 42 records for the wrong reason. One file-level quarantine with a `schema` reason is the true picture and means one resend from upstream.

## Dates

Date rules compare against the **file's business date** taken from its name (`2026-11-25`), never today's date.
A rerun next month therefore routes every record exactly as it did today. With today's date, line 18 (issued `2026-12-25`) would pass R-16 once that day had gone by, every on-time line would turn late after 30 days, and each rerun would route the same file differently.
A file whose name has no parseable date should be quarantined whole (schema), because there is nothing for the date rules to compare against.
