# Day 1 (Module 1) - lab solutions

| Lab | What "done" looks like | Solution files |
|---|---|---|
| Lab 0 - quick-start | `verify_setup.sh` shows PASS for tools, region and profile `accelya-ai`; "profile accelya not set yet" is expected until Lab 2. Branch `day1-<id>` exists. | (no file; see the trainer script) |
| Lab 1 - prompt → architecture options | Filled prompt, one Bedrock call, each hop marked OK / VAGUE / WRONG, both options scored on the 5 HITL dimensions, a decision that follows from the scores | `../samples/m1-arch-options-solution.md` (filled prompt) · `../samples/m1-arch-options-sample-output.md` (sample answer) · `../samples/HITL_REVIEW-m1-arch-options-sample.md` (worked review, 8/15 → 12/15) |
| Lab 2 - change, test, deploy, trace | `test_ingest.py` 4 PASS, stack deployed, three uploads, cold/warm request IDs and `airline_code` AI, BA, EK in the review file | `lab2-app-solution.py` (handler with the change) · `lab2-m1-review-solution.md` |
| Lab 3 - trust vs identity | ok → denied → ok log lines, a one-line diagnosis naming the trust policy's ExternalId condition, six answers for hop 2; stretch A and B answered | `lab3-m1-review-solution.md` |

Try it yourself before opening these. To check the Lab 2 solution:
`cp day1/solutions/lab2-app-solution.py day1/src/ingest/app.py && python3 day1/tests/test_ingest.py`
(then `git checkout day1/src/ingest/app.py` to put your own back).
