# M3 Lab 1 - raw zone, trigger, duplicate vs resend: evidence

**Learner:** `<your-id>`   **Account (aws sts get-caller-identity):** `…last 4 digits`   **Bucket:** `accelya-m1-<you>-<acct>`

Commands: day3/LABS.md (Lab 1). Write counts and identifiers only - never row values.

| Step | Correlation id | file.received count (grep by that id) | Expected |
|---|---|---|---|
| Land in raw/ | `<you>-d3-001` | | 1 |
| Bucket root (filter read first: prefix `____`) | `<you>-d3-002` | | 0 |
| Replay the event once (simulated duplicate delivery) | `<you>-d3-001` | | 2 in total |
| Resend: new upload, same bytes | `<you>-d3-003` | | 1 |

- ETag 1 (after landing): `____`    ETag 2 (after the resend): `____`    equal? `yes / no` - what does that tell you about this file?
- Paste ONE file.received line (keys and counts only):

## Duplicate delivery vs resend (one line each)
- Duplicate delivery is …
- A resend is …
- How should Day 4's validator recognise each one, so nothing is settled twice?

## The zone layout you'd design
| Zone | Prefix | Who writes | Who reads | Kept for | Why it exists |
|---|---|---|---|---|---|
| raw | `raw/` | | | | |
| validated | | | | | |
| quarantine | | | | | |
| curated | | | | | |
