# Validation rule types (day3/scripts/classify.py)

Every rule is one JSON object with `id`, `type`, the fields below, `reason`, `on_fail` and a one-line `why`.

| type | Fields | Passes when |
|---|---|---|
| `required` | `fields: [..]` | every listed field is non-empty |
| `regex` | `field`, `pattern` | the whole value matches the pattern |
| `in_ref` | `field`, `ref` (`airlines`, `fare_classes`, `currencies`) | the value is in `day3/data/ref/<ref>.json` |
| `od_airports_in_ref` | `field` | both airports of `AAA-BBB` are in `airports.json` |
| `od_distinct` | `field` | origin ≠ destination |
| `ticket_prefix` | `field`, `airline_field` | the ticket's 3-digit prefix is that airline's `ticket_prefix` |
| `decimal_places` | `field`, `max_places` | a number with at most that many decimals |
| `min_exclusive` | `field`, `value` | the number is greater than `value` |
| `date_format` | `field`, `format` (default `%Y-%m-%d`) | the value parses as that date format |
| `date_not_after_file_date` | `field` | the date is on or before the file's business date (from its name) |
| `date_within_days` | `field`, `days` | the date is no more than `days` before the file's business date |
| `unique` | `field` | the value hasn't appeared in an earlier, accepted record of the same file |

File rules (`file_rules`): `header` with `columns: [..]` - the header must match exactly, or the whole file is quarantined.

## reason and on_fail

| reason | What it means | Usual `on_fail` |
|---|---|---|
| `required` | a mandatory field is empty | `quarantine` |
| `format` | the value has the wrong shape | `quarantine` |
| `referential` | the value doesn't exist in reference data | `quarantine` |
| `business` | well-formed and known, but breaks a business rule | `quarantine` |
| `late` | valid but outside the normal window | your decision: `flag` or `quarantine` |
| `duplicate` | already seen in this file | your decision: `skip` or `quarantine` |

`on_fail`: `quarantine` → `quarantine/<reason>/`, `flag` → `validated/` with a flag, `skip` → not written, counted in the manifest.

Rules run in phase order (required → format → referential → business → late → duplicate), whatever order you list them in.
The first failing quarantine rule decides the reason. Dates are compared with the **file's** business date, never today's
date, so a rerun next month gives the same answer.
