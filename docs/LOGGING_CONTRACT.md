# Logging contract v1.1

Issued on Day 2. Every artefact from Day 2 onwards is reviewed against it, and it is a line item on every HITL review.
Machine-readable version: `docs/logging-contract.json`. Checker: `python3 day2/scripts/check_logs.py`.

## 1. One shape

Every **application log record** is **one JSON object**, at most **2 KB**. Lambda's own platform records
(`START`, `END`, `REPORT`, `INIT_START`…) are not application records and are not checked. `aws logs tail --format short`
prints a timestamp in front of each record; the checker strips it before parsing.

```json
{"ts": "2026-11-24T05:12:09Z", "level": "INFO", "service": "accelya-m2-cancellation",
 "correlation_id": "c-7f3a91", "boundary": "sqs->lambda", "message": "cancellation.decided",
 "event_id": "evt-1001", "fare_class": "K", "refund_rule_id": "R-SAVER", "decision": "APPROVE"}
```

| Key | Rule |
|---|---|
| `ts` | UTC, ISO 8601, ends in `Z` |
| `level` | `DEBUG`, `INFO`, `WARNING` or `ERROR` |
| `service` | `accelya-m<module>-<name>` |
| `correlation_id` | **Mandatory on every line.** Taken from the message attribute or object metadata; minted only if absent |
| `boundary` | The hop this line belongs to, `source->target` (e.g. `sqs->lambda`, `lambda->sts`), or `internal` |
| `message` | A dotted event name, lowercase: `cancellation.decided`, `batch.completed` - never a sentence |

## 2. Identifiers and counts only - never payloads

Allowed extra keys are listed in `logging-contract.json` (`optional`). **Anything else is a violation**, so a payload can't leak in under a new name.

An allowed key isn't a free-for-all either. Every key has a **value rule** (`value_rules`: type, pattern, enum or max length):
`message` must be a dotted event name of at most 64 chars, `decision` one of three values, `pnr` six characters, and so on.
That stops a payload hiding inside an allowed key such as `message`. Objects and arrays are never allowed.
`python3 day2/tests/test_checker.py` proves it with negative tests.

Never log: the event or message body, file rows, passenger names, emails, phone numbers, card data, fare amounts, raw exception messages.

## 3. Levels mean something

| Level | Use for |
|---|---|
| `DEBUG` | Local diagnosis only. Off in deployed functions |
| `INFO` | Boundary crossings and outcomes: received, decided, completed |
| `WARNING` | Recoverable degradation: retried, defaulted, sent to manual review |
| `ERROR` | The failure path. **Must include a non-empty `error_type`** (exception class or classification) |

## 4. Why no payloads

1. **PII.** Logs are read by more people and kept longer than the data store. A passenger name in a log is a data leak.
2. **Cost.** CloudWatch charges per GB ingested. Logging the payload multiplies log volume by the payload size, for every message.
3. **Truncation and loss.** A CloudWatch log event is capped at 1 MB. Big lines get cut or rejected exactly when you need them.

## 5. Lambda platform logs and Powertools (Day 4)

- Platform records (`START`/`END`/`REPORT`) are written by Lambda, not by you. The contract covers only what your code writes.
- From Day 4 we use **Lambda Powertools Logger**. Out of the box it writes `timestamp` (not `ts`) and adds keys such as
  `location`. On Day 4 we configure it (a small custom formatter) to emit exactly this contract's shape, and the checker is how
  you'll prove it. Until then, write records with `json.dumps(dict)`.

## 6. Changing the contract

Need a new key? Open a pull request that edits `logging-contract.json`, bumps `version`, and says why. Don't just log it.

> **No nulls.** If a key has no value, leave it out. `"error_type": null` or `"pnr": null` is non-conformant.
