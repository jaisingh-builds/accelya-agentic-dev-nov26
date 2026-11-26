# M3 Lab 2 - validation rule design (paper first)

**Learner:** `chandra`   **File:** `day3/data/ticket_issue_AI_2026-11-25.csv` (synthetic)

Design every rule on paper **before** you write JSON. One row per rule. Use the five outline areas first,
then add anything else the file needs. Categories: `required` · `format` · `referential` · `business` · `late` · `duplicate`.
Routes: `quarantine` (→ `quarantine/<category>/`) · `flag` (→ `validated/` with a flag) · `skip` (not written, counted).

| Rule id | Area | Field | Check (plain English) | Category | Route | Why (one line) |
|---|---|---|---|---|---|---|
| R-01 | all | all nine columns | must not be empty | required | quarantine | nothing to settle or trace without them |
| R-02 | PNR | pnr | 6 upper-case letters/digits | format | quarantine | PNR is the ordering key downstream; a malformed one breaks grouping |
| R-03 | airline code | airline_code | exactly 2 upper-case letters/digits | format | quarantine | IATA code shape; a 3-char code can never resolve to a contract |
| R-04 | airline code | airline_code | exists in `airlines.json` | referential | quarantine | we only settle airlines we have a contract for |
| R-05 | fare class | fare_class | one upper-case letter | format | quarantine | fare class is a single IATA booking letter |
| R-06 | fare class | fare_class | exists in `fare_classes.json` | referential | quarantine | unknown booking class means refund % cannot be looked up |
| R-07 | OD pair | od_pair | shape `AAA-BBB` (3 letters, hyphen, 3 letters) | format | quarantine | split must yield exactly two airport codes |
| R-08 | OD pair | od_pair | both airports in `airports.json` | referential | quarantine | an unknown airport cannot be priced or settled |
| R-09 | OD pair | od_pair | origin ≠ destination | business | quarantine | same-city ticket is a data or fraud problem, not a route we sell |
| R-10 | fare break | fare_amount | parses as a number, max 2 decimal places | format | quarantine | money is Decimal; >2dp is a producer defect |
| R-11 | fare break | fare_amount | strictly greater than 0 | business | quarantine | a negative or zero fare must never reach refund maths |
| R-12 | fare break | currency | exists in `currencies.json` | referential | quarantine | settlement only accepts the agreed ISO-4217 list |
| R-13 | ticket | ticket_number | shape `NNN-NNNNNNNNNN` (3 digits, hyphen, 10 digits) | format | quarantine | ticket stock/accounting code + serial number |
| R-14 | ticket | ticket_number | 3-digit prefix equals the airline's `ticket_prefix` | referential | quarantine | e.g. a 125 (BA) ticket on an AI row means wrong airline mapping |
| R-15 | issue date | issue_date | parses as `YYYY-MM-DD` | format | quarantine | every downstream date comparison needs ISO |
| R-16 | issue date | issue_date | on or before the file's business date | business | quarantine | a future issue date is a producer clock or fraud problem |
| R-17 | issue date | issue_date | within 30 days of the file's business date | late | flag | valid but outside the normal window: settle it, flag for ops |
| R-18 | agency | agency_iata | exactly 8 digits | format | quarantine | IATA agency codes are 8 numeric digits |
| R-19 | ticket | ticket_number | seen earlier in this file | duplicate | skip | same ticket twice: keep the first, skip and count the repeat |
| F-01 | whole file | header | columns exactly as agreed | schema | quarantine whole file | a renamed/missing column breaks every rule after it |

## Three routing decisions to justify (one or two lines each)

1. **Late ticket** (valid, issued 56 days before the file date): **flag**. The record is well-formed and the
   ticket is real; the delay is an upstream/process problem. It should still settle, but ops must see it,
   so it goes to `validated/` carrying flag `R-17` and counts as `flagged` in the manifest.
2. **Duplicate ticket in the same file**: **skip**, not quarantine. The first copy is already the decision;
   a second write would double-count the same ticket. The manifest must show `duplicates: 1` (plus the
   line it duplicates, in the sidecar) so `rows_in = out` still balances.
3. **Header renamed** (`currency` → `fare_currency`): **reject the whole file**. A renamed column is a
   contract change by the producer; no record can be evaluated against a schema that no longer matches.
   The file goes to `quarantine/schema/` and the producer must fix the feed.

## Dates

All date rules compare against the **file's business date** (parsed from the file name, `2026-11-25`),
never today's date. A rerun next month produces identical routing - deterministic and replayable.
