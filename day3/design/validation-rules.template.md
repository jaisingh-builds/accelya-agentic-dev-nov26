# M3 Lab 2 - validation rule design (paper first)

**Learner:** `<your-id>`   **File:** `day3/data/ticket_issue_AI_2026-11-25.csv` (synthetic)

Design every rule on paper **before** you write JSON. One row per rule. Use the five outline areas first,
then add anything else the file needs. Categories: `required` · `format` · `referential` · `business` · `late` · `duplicate`.
Routes: `quarantine` (→ `quarantine/<category>/`) · `flag` (→ `validated/` with a flag) · `skip` (not written, counted).

| Rule id | Area | Field | Check (plain English) | Category | Route | Why (one line) |
|---|---|---|---|---|---|---|
| R-01 | all | pnr, ticket_number, ... | must not be empty | required | quarantine | nothing to settle or trace without them |
| R-02 | PNR | pnr | 6 upper-case letters/digits | format | quarantine | |
| R-03 | airline code | airline_code | 2 characters, letters or digits (digits allowed, e.g. 6E) | format | quarantine | an IATA airline code is always 2 characters |
| R-10 | airline code | airline_code | is in the airlines reference list | referential | quarantine | we only settle airlines we have a contract for |
| R-04 | fare class | fare_class | exactly 1 upper-case letter | format | quarantine | booking classes are single capital letters |
| R-11 | fare class | fare_class | is in the published fare class list | referential | quarantine | a class the airline does not publish cannot be priced or refunded |
|  | OD pair | od_pair |  |  |  | |
|  | OD pair | od_pair |  |  |  | |
|  | OD pair | od_pair |  |  |  | |
|  | fare break | fare_amount |  |  |  | |
|  | fare break | fare_amount |  |  |  | |
|  | fare break | currency |  |  |  | |
|  | ticket | ticket_number |  |  |  | |
|  | ticket | ticket_number |  |  |  | |
|  | issue date | issue_date |  |  |  | |
|  | issue date | issue_date |  |  |  | |
|  | issue date | issue_date |  |  |  | |
|  | agency | agency_iata |  |  |  | |
|  | ticket | ticket_number | seen earlier in this file | duplicate |  | |
| F-01 | whole file | header | columns exactly as agreed | schema | quarantine whole file | |

## Three routing decisions to justify (one or two lines each)

1. **Late ticket** (valid, issued 56 days before the file date): quarantine or flag? Why?
2. **Duplicate ticket in the same file**: quarantine or skip? What must the manifest show?
3. **Header renamed** (`currency` → `fare_currency`): reject one record, or the whole file? Why?

## Dates

Which date do your date rules compare against: today's date, or the file's business date? What happens on a rerun next month?
