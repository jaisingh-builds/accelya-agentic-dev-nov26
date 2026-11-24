# M1 Lab 3 - solution (example evidence file)

> Expected shape of `day1/reviews/m1-lab3-<id>.md`. Paste YOUR three log lines; the diagnosis and the
> six answers below are the model answer.

```
# M1 Lab 3 - trainer
ok line:      {"level": "INFO",  "message": "probe.assume_role.ok", "boundary": "lambda->sts", "correlation_id": "m1-...", ...}
denied line:  {"level": "ERROR", "message": "probe.assume_role.denied", "boundary": "lambda->sts", "error_code": "AccessDenied", "external_id_sent": false, ...}
fixed line:   {"level": "INFO",  "message": "probe.assume_role.ok", "boundary": "lambda->sts", ...}
Diagnosis: trust policy - reader-role demands sts:ExternalId; caller sent none (aws iam get-role shows the Condition).
```

## Hop 2: Lambda -> STS (the six service-boundary answers)

| # | Answer |
|---|---|
| 1 Push/pull | Push: the Lambda calls STS. |
| 2 Sync/async | Sync: blocked until credentials return. |
| 3 Payload | `RoleArn`, `RoleSessionName`, `ExternalId` → temporary credentials (1 hour). |
| 4 Role | reader-role's trust policy names ingest-role (enough in one account); ingest-role also has an explicit `sts:AssumeRole` allow (needed cross-account). |
| 5 Failure | AccessDenied logged at ERROR with `boundary lambda->sts`; no retry: it's a configuration error, not a transient one. |
| 6 Corr. ID | On our application log lines; `RoleSessionName` shows the learner in CloudTrail. |

## Supporting evidence from the same run

- `probe.list.ok` on `validated/` and `probe.list.denied` on `raw/`: least privilege proven by two log lines.
- `trust_mode=locked` without `ProbeExternalId` → denied; with `ProbeExternalId=accelya-m1-partner` → ok.
  Nothing changed on ingest-role; only the trust policy gained a Condition.

## Stretch A (prediction): remove IngestCanAssumeReader, redeploy - does AssumeRole still work?

**Yes, it still works.** Same account, and the trust policy names ingest-role's ARN directly, which is
sufficient on its own. Identity-policy allows are mandatory **cross-account**, not same-account.
Restore with `git checkout day1/template.yaml` and redeploy.

## Stretch B: the duplicate stack

`describe-stack-events` lists **newest first**, so read from the bottom up. The cause is the earliest
`CREATE_FAILED` with a real reason: a resource that **already exists** (bucket, role or log group named
from the same LearnerId). Later "Resource creation cancelled" lines and `ROLLBACK_IN_PROGRESS` are
consequences. Stack ends `ROLLBACK_COMPLETE`; delete it with
`sam delete --stack-name accelya-m1-$L-dup --no-prompts`.

## Fallback mode (no IAM roles in the sandbox)

Diagnose `day1/demo/probe_trace_example.log` (saved allow → deny → allow) with the same diagnosis
line and the same six answers. Record as "diagnosed from trace".
