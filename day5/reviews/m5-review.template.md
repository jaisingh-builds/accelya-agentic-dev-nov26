# M5 review - <reviewer-id> reviewing <author-id> (branch day5-<author-id>)

| # | Dimension | Score 1-5 | Evidence (file:line or command output) |
|---|-----------|-----------|----------------------------------------|
| 1 | Selection: each choice backed by a number from the table |  |  |
| 2 | Dockerfile: pinned slim base, cache order, non-root, nothing extra copied |  |  |
| 3 | Run evidence: STOPPED, exit 0, logs CONFORMANT, 30 lines with the run's correlation_id |  |  |
| 4 | Data: 606 in = 606 out, 7 partitions, money still decimal(12,2) |  |  |
| 5 | Queries: check_recon 2/2, view derives its cut-off, no hard-coded date |  |  |

**Logging contract conformance (mandatory):** pass / fail - evidence:

**Verdict:** approve / changes requested

**The one fix that matters most:**
