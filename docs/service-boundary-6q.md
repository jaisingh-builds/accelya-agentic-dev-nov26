# The six service-boundary questions

Answer these for **every hop** between two services, **before** anyone writes code.
Used in every module of this course. Paste the table into your design or PR.

| # | Question | What a good answer names |
|---|---|---|
| 1 | **Push or pull?** | Who starts the call: the producer invokes the consumer (push) or the consumer polls (pull). |
| 2 | **Sync or async?** | Is the caller blocked waiting? What is the timeout? |
| 3 | **Payload shape?** | The contract: fields, who versions it, what stays backward compatible. |
| 4 | **Which role?** | The IAM principal making the call, and the minimum actions + resources it needs. |
| 5 | **Failure path?** | What happens on error: retry (how many), quarantine, DLQ, manual review. |
| 6 | **Correlation ID?** | Where it lives on this hop and how it survives into the next one. |

## Worked example - Day 1 hop: S3 -> Lambda

| # | Answer |
|---|---|
| 1 | Push. S3 event notification invokes the Lambda. |
| 2 | Async. S3 does not wait; Lambda retries async events twice on error. |
| 3 | S3 event JSON (`Records[].s3.bucket.name`, `object.key`, `size`). AWS owns and versions it. |
| 4 | S3 needs `lambda:InvokeFunction` (resource policy SAM adds). Lambda's role needs `s3:GetObject` on `raw/*` only. |
| 5 | Lambda retries a failed *asynchronous* invoke 2 more times, then discards the event unless an on-failure destination or DLQ is configured (none today). SQS redrive policies (Day 6) are a different mechanism. |
| 6 | Object metadata `x-amz-meta-correlation-id` if the uploader set one; else minted by the Lambda and written on every application log line (Lambda's own START/REPORT lines don't carry it). |

## Template

```
Hop: <service A> -> <service B>
1 Push/pull :
2 Sync/async:
3 Payload   :
4 Role      :
5 Failure   :
6 Corr. ID  :
```
