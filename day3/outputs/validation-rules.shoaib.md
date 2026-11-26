# M3 Lab 2 - validation rule design (paper first)

**Learner:** Shoaib   **File:** `day3/data/ticket_issue_AI_2026-11-25.csv` (synthetic)

Design every rule on paper **before** you write JSON. One row per rule. Use the five outline areas first,
then add anything else the file needs. Categories: `required` · `format` · `referential` · `business` · `late` · `duplicate`.
Routes: `quarantine` (→ `quarantine/<category>/`) · `flag` (→ `validated/` with a flag) · `skip` (not written, counted).

| Rule id | Area | Field | Check (plain English) | Category | Route | Why (one line) |
|---|---|---|---|---|---|---|
| R-01 | all | pnr, ticket_number, ... | must not be empty | required | quarantine | nothing to settle or trace without them |
| R-02 | PNR | pnr | 6 upper-case letters/digits | format | quarantine | `AB12C` (5 chars) and `ab12cd` (lower case) cannot be matched to the booking |
| R-03 | airline code | airline_code | exactly 2 upper-case letters or digits (e.g. `6E`) | format | quarantine | `AIR` is a 3-letter code, not an IATA airline code |
| R-04 | airline code | airline_code | must exist in `airlines.json` (AI, BA, EK) | referential | quarantine | `XX` is well-formed but unknown, so no ticket prefix or settlement partner |
| R-05 | fare class | fare_class | exactly 1 upper-case letter | format | quarantine | `k` (lower case) would not match the fare table |
| R-06 | fare class | fare_class | must exist in `fare_classes.json` | referential | quarantine | `R` is one letter but not a class AI publishes |
| R-07 | OD pair | od_pair | shape `AAA-BBB` (3 letters, hyphen, 3 letters) | format | quarantine | `BOMDXB` has no separator, so origin and destination cannot be split |
| R-08 | OD pair | od_pair | both airports must exist in `airports.json` | referential | quarantine | `BOM-XYZ` has an unknown airport, so the route cannot be priced |
| R-09 | OD pair | od_pair | origin must differ from destination | business | quarantine | `BOM-BOM` is not a journey |
| R-10 | fare break | fare_amount | a number with at most 2 decimal places | format | quarantine | `4520.555` cannot be paid exactly; money is `Decimal` with `ROUND_HALF_UP`, so we reject it rather than guess a rounding |
| R-11 | fare break | fare_amount | must be greater than 0 | business | quarantine | `-1200.00` is a refund or an error and does not belong in an issue file |
| R-12 | fare break | currency | must exist in `currencies.json` | referential | quarantine | `INX` is not an ISO 4217 code, so the amount has no unit |
| R-13 | ticket | ticket_number | shape `NNN-NNNNNNNNNN` (3 digits, hyphen, 10 digits) | format | quarantine | `098-75716779` is short and cannot be a valid ticket number |
| R-14 | ticket | ticket_number | 3-digit prefix must equal the airline's `ticket_prefix` (AI = 098) | referential | quarantine | `125-...` is British Airways' prefix on an Air India row |
| R-15 | issue date | issue_date | must parse as `YYYY-MM-DD` | format | quarantine | `25/11/2026` is ambiguous (day-first vs month-first) so it is not guessed |
| R-16 | issue date | issue_date | must not be after the file's business date | business | quarantine | `2026-12-25` is a ticket issued in the future |
| R-17 | issue date | issue_date | no more than 30 days before the file's business date | late | flag | `2026-09-30` is valid but 56 days old, so a person should look at it |
| R-18 | agency | agency_iata | exactly 8 digits | format | quarantine | `1234567` has 7 digits, so the agency cannot be identified for settlement |
| R-19 | ticket | ticket_number | seen earlier in this file | duplicate | skip | the second `JHELYR` / `098-2015079145` row is the same ticket sent twice, so counting it twice would double-settle it |
| F-01 | whole file | header | columns exactly as agreed | schema | quarantine whole file | every field is read by name, so a renamed column makes every row untrustworthy |

Agreed header for F-01: `pnr,ticket_number,airline_code,fare_class,od_pair,issue_date,fare_amount,currency,agency_iata`.

Expected result on this file if the rules are right: 42 rows = **22 validated** (21 clean + 1 flagged by R-17) + **19 quarantined** + **1 duplicate skipped**.

## Three routing decisions to justify (one or two lines each)

1. **Late ticket** (valid, issued 56 days before the file date): quarantine or flag? Why?
   **Flag.** The ticket is valid and the money is probably real, so quarantining would hold up a legitimate settlement. It goes to `validated/` with a flag so a person can review it.
2. **Duplicate ticket in the same file**: quarantine or skip? What must the manifest show?
   **Skip.** Both rows are identical, so the first one already carries the information and nothing is lost. The manifest must count it (`duplicates_skipped: 1`) so the totals still balance: validated + quarantined + skipped = rows read.
3. **Header renamed** (`currency` → `fare_currency`): reject one record, or the whole file? Why?
   **The whole file.** A renamed header affects every row the same way, so no row can be read reliably. Accepting some would hide a producer change that needs fixing at the source.

## Dates

Which date do your date rules compare against: today's date, or the file's business date? What happens on a rerun next month?

**The file's business date**, taken from the file name (`2026-11-25`), never from today's date. A rerun next month then gives the same answer, because R-16 and R-17 depend only on the file's content and name. If we used today's date, every ticket in a rerun would look late and the results would change.
