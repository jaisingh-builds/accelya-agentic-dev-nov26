# M1 Lab 2 - solution (example evidence file)

> Expected shape of `day1/reviews/m1-lab2-<id>.md`. Your request IDs and timings will differ;
> what matters is that each line is evidence you read from the logs.

```
# M1 Lab 2 - trainer
test: 4 PASS after change (diff: +4 lines in day1/src/ingest/app.py, see lab2-app-solution.py)
cold request IDs: 3f2a9c1e-...  (REPORT shows Init Duration ~400 ms)   warm: 7b81d0aa-..., c04e5f12-...
max memory used: ~85 of 256 MB
airline_code seen: AI, BA, EK
```

## The code change (the only lines that matter)

```python
name = key.rsplit("/", 1)[-1]                      # works for raw/ and raw/run1/ keys
parts = name.split("_")                            # ticket_issue_<AIRLINE>_<date>.csv
airline = parts[2] if name.startswith("ticket_issue_") and len(parts) > 3 else "unknown"
# ...and airline_code=airline added to the existing file.received emit(...)
```

## Why this is the solution, not just *a* solution

- One new field on the **existing** `file.received` line, not a new log line (the test checks that line).
- Other key shapes fall back to `"unknown"` instead of raising IndexError: an explicit error path (AGENTS.md).
- No new function, class or dependency: no speculative abstraction.

## Answers to the questions asked in the lab

- **Cold vs warm:** only the first invocation after a deploy (or after idle) has an `Init Duration` in
  its REPORT line and `cold_start: true` in our log. Billed duration includes the init time.
- **Upload to the bucket root?** Nothing happens: the S3 event filter is the `raw/` prefix.
