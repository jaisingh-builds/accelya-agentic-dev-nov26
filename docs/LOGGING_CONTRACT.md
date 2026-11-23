# Logging contract v1.0

Issued on Day 2. Every artefact from Day 2 onwards is reviewed against it, and it is a line item on every HITL review.
Machine-readable version: `docs/logging-contract.json`. Checker: `python3 day2/scripts/check_logs.py`.

## 1. One shape

Every application log line is **one JSON object on one line**, at most **2 KB**.

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
Values must be scalars (string, number, boolean, null) - no objects or arrays.

Never log: the event or message body, file rows, passenger names, emails, phone numbers, card data, fare amounts, raw exception messages.

## 3. Levels mean something

| Level | Use for |
|---|---|
| `DEBUG` | Local diagnosis only. Off in deployed functions |
| `INFO` | Boundary crossings and outcomes: received, decided, completed |
| `WARNING` | Recoverable degradation: retried, defaulted, sent to manual review |
| `ERROR` | The failure path. **Must include `error_type`** (exception class or classification) |

## 4. Why no payloads

1. **PII.** Logs are read by more people and kept longer than the data store. A passenger name in a log is a data leak.
2. **Cost.** CloudWatch charges per GB ingested. Logging the payload multiplies log volume by the payload size, for every message.
3. **Truncation and loss.** A CloudWatch log event is capped at 1 MB. Big lines get cut or rejected exactly when you need them.

## 5. Changing the contract

Need a new key? Open a pull request that edits `logging-contract.json`, bumps `version`, and says why. Don't just log it.
