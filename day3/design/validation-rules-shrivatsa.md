# M3 Lab 2 - validation rule design (paper first)

**Learner:** `shrivatsa`   **File:** `day3/data/ticket_issue_AI_2026-11-25.csv` (synthetic)

Design every rule on paper **before** you write JSON. One row per rule. Use the five outline areas first,
then add anything else the file needs. Categories: `required` · `format` · `referential` · `business` · `late` · `duplicate`.
Routes: `quarantine` (→ `quarantine/<category>/`) · `flag` (→ `validated/` with a flag) · `skip` (not written, counted).

| Rule id | Area | Field | Check (plain English) | Category | Route | Why (one line) |
|---|---|---|---|---|---|---|
| R-01 | all | pnr, ticket_number, airline_code, fare_class, od_pair, issue_date, fare_amount, currency, agency_iata | must not be empty | required | quarantine | nothing to settle or trace without them |
| R-02 | PNR | pnr | 6 upper-case letters/digits (`^[A-Z0-9]{6}$`) | format | quarantine | a malformed PNR cannot be traced back to the booking |
| R-03 | airline code | airline_code | exactly 2 upper-case letters (`^[A-Z]{2}$`) | format | quarantine | catches shape errors such as `AIR` separately from unknown carriers |
| R-04 | airline code | airline_code | must be in airlines.json (AI, BA, EK) | referential | quarantine | an unknown carrier has no ticket prefix to settle against |
| R-05 | fare class | fare_class | exactly 1 upper-case letter (`^[A-Z]$`) | format | quarantine | catches lower-case, digits and multi-letter values as format errors |
| R-06 | fare class | fare_class | must be in fare_classes.json | referential | quarantine | a booking class the carrier does not publish cannot be priced or settled |
| R-07 | OD pair | od_pair | shape `AAA-BBB` (`^[A-Z]{3}-[A-Z]{3}$`) | format | quarantine | the airport rules cannot run on a malformed pair |
| R-08 | OD pair | od_pair | both airports must be in airports.json | referential | quarantine | an unverified route cannot be settled |
| R-09 | OD pair | od_pair | origin must differ from destination | business | quarantine | a ticket from an airport to itself is not a valid journey |
| R-10 | fare break | fare_amount | a number with at most 2 decimal places | format | quarantine | avoids a rounding decision on money; amounts are Decimal, never float |
| R-11 | fare break | fare_amount | must be greater than 0 | business | quarantine | an issue file should not carry zero or negative fares; refunds are a different feed |
| R-12 | fare break | currency | must be in currencies.json (INR, USD, AED, GBP, EUR, SGD) | referential | quarantine | an unknown currency cannot be converted or settled |
| R-13 | ticket | ticket_number | shape `NNN-NNNNNNNNNN` (`^[0-9]{3}-[0-9]{10}$`) | format | quarantine | malformed tickets are caught as `format` before the prefix check |
| R-14 | ticket | ticket_number | first 3 digits must be the row airline's `ticket_prefix` (AI 098, BA 125, EK 176) | referential | quarantine | a ticket numbered for another carrier would be settled against the wrong airline |
| R-15 | issue date | issue_date | ISO date `YYYY-MM-DD` | format | quarantine | one agreed format avoids day/month ambiguity such as `03/04` |
| R-16 | issue date | issue_date | not after the file's business date | business | quarantine | a ticket cannot be issued after the file that reports it |
| R-17 | issue date | issue_date | no more than 30 days before the file's business date | late | flag | valid revenue is still settled, but a person can see it was late |
| R-18 | agency | agency_iata | exactly 8 digits (`^[0-9]{8}$`) | format | quarantine | without a valid agency the sale cannot be attributed to an agent |
| R-19 | ticket | ticket_number | seen earlier in this file | duplicate | skip | the first copy is settled; the repeat is counted, not written twice |
| F-01 | whole file | header | the 9 columns exactly as agreed | schema | quarantine whole file | a renamed column means the file is not in the agreed contract |

Rules run in phase order (required → format → referential → business → late → duplicate), whatever order they are listed in.

## Three routing decisions to justify (one or two lines each)

1. **Late ticket** (valid, issued 56 days before the file date): **flag** (R-17, 30-day window, so 56 days is late).
   The ticket is valid and its revenue should still be settled. Quarantining would block good money for a timing issue.
   The flag makes it visible to a reviewer.
2. **Duplicate ticket in the same file**: **skip** (R-19). The first occurrence is settled and the repeat is not written
   anywhere. The manifest must show a duplicate-skipped count, so that
   rows in = validated (clean + flagged) + quarantined + duplicates skipped, and nothing disappears silently.
3. **Header renamed** (`currency` → `fare_currency`): reject the **whole file** (F-01). A renamed column means the file
   is not in the agreed contract, so no row can be trusted to line up. One clear schema failure is better than
   thousands of identical per-row `required` failures.

## Dates

The date rules (R-15 to R-17) compare against the **file's business date**, taken from the file name
(`ticket_issue_AI_2026-11-25.csv` → 2026-11-25), never today's date. A rerun next month therefore gives exactly the
same result for the same file, because the rules never look at the clock. Comparing with today's date would make every
ticket look late on a rerun, so the same file would route differently.
