# Architecture options (3) - SAMPLE (Lab 1 fallback)

_Pre-written sample, NOT a live model answer. Use it only if your Bedrock call fails or returns fewer
than three options. Copy the scorecard template and score these three options exactly as you would
your own (15 cells, one line of evidence each). Synthetic scenario; no real Accelya data._

_Prompt fill-ins assumed: volume 2,000 cancellations/day with peaks of 50/minute after a disruption;
ordering matters per PNR (a re-booking must not overtake its cancellation); extra non-goal: no Step
Functions today._

## Bedrock answer (sample)

Each hop answers the six service-boundary questions (docs/service-boundary-6q.md): push or pull ·
sync or async · payload · role · failure path · correlation ID.

### Option A - SQS Standard -> Lambda -> DynamoDB

Summary: a Standard queue buffers cancellations; one Lambda decides with partial batch responses and
writes each decision to a DynamoDB table the settlement team queries.

Services in order: producer -> SQS Standard (+ DLQ) -> Lambda `decide` -> DynamoDB `refund_decisions`

| Hop | Push/pull | Sync/async | Payload | Role | Failure path | Correlation ID |
|---|---|---|---|---|---|---|
| producer -> SQS | push | sync SendMessage | cancellation JSON | producer role: sqs:SendMessage on this queue | SDK retries, then the producer gets an error | message attribute `correlation_id` |
| SQS -> Lambda | pull (event source mapping) | sync per batch | up to 10 records | Lambda role: sqs:ReceiveMessage/DeleteMessage | ReportBatchItemFailures; redelivery after visibility timeout; DLQ after maxReceiveCount = 3 | read from message attributes |
| Lambda -> DynamoDB | push | sync PutItem | decision row (no PII) | Lambda role: dynamodb:PutItem on one table | SDK retries, then the record goes to batchItemFailures | stored as an attribute on the row |

Risks: (1) Standard queues do not guarantee order, so a re-booking can overtake its cancellation for the
same PNR. (2) At-least-once delivery means a decision can be written twice; needs a conditional write on
event_id.

Cost driver: Lambda invocations and DynamoDB writes, both pay-per-request and small at 2,000/day.

### Option B - EventBridge -> Lambda (async) -> DynamoDB + EventBridge

Summary: producers put cancellation events on a custom event bus; a rule invokes the decider
asynchronously; decisions are stored and re-published as `refund.decided` events for settlement.

Services in order: producer -> EventBridge bus -> rule -> Lambda `decide` (on-failure destination: SQS DLQ)
-> DynamoDB + EventBridge `refund.decided` -> SQS `settlement-feed`

| Hop | Push/pull | Sync/async | Payload | Role | Failure path | Correlation ID |
|---|---|---|---|---|---|---|
| producer -> EventBridge | push | sync PutEvents | event `detail` | producer role: events:PutEvents on this bus | SDK retries; check FailedEntryCount in the response | in `detail.correlation_id` |
| EventBridge -> Lambda | push | async | one event | resource policy lets the rule invoke the function | Lambda async retries (2 more), then on-failure destination SQS DLQ | read from `detail` |
| Lambda -> DynamoDB | push | sync PutItem | decision row | dynamodb:PutItem on one table | SDK retries, then exception -> async retry | attribute on the row |
| Lambda -> EventBridge -> SQS | push | sync PutEvents, then async rule target | decision event | events:PutEvents; queue policy for the rule | EventBridge target retries, then the rule's DLQ | copied into the new event's `detail` |

Risks: (1) no ordering per PNR at all. (2) Two writes per decision (table + event) can disagree if the
second fails after the first succeeds.

Cost driver: EventBridge events (two per cancellation) plus Lambda; still small at this volume.

### Option C - SQS FIFO (group = PNR) -> Lambda -> DynamoDB -> Streams -> settlement

Summary: a FIFO queue with MessageGroupId = pnr keeps each PNR's events in order; the decider writes to
DynamoDB; a stream-triggered Lambda forwards each decision to the settlement queue.

Services in order: producer -> SQS FIFO (+ DLQ) -> Lambda `decide` -> DynamoDB -> DynamoDB Streams ->
Lambda `publish` -> SQS `settlement-feed`

| Hop | Push/pull | Sync/async | Payload | Role | Failure path | Correlation ID |
|---|---|---|---|---|---|---|
| producer -> SQS FIFO | push | sync SendMessage (group = pnr, dedup id = event_id) | cancellation JSON | sqs:SendMessage on this queue | SDK retries (dedup absorbs duplicates for 5 minutes), then the producer gets an error | message attribute |
| SQS FIFO -> Lambda | pull (event source mapping) | sync per batch | batch, in order per group | Lambda role: receive/delete | ReportBatchItemFailures: return the failed record AND every later one; DLQ after maxReceiveCount = 3 | message attribute |
| Lambda -> DynamoDB | push | sync PutItem (condition: event_id not seen) | decision row | dynamodb:PutItem | SDK retries, then batchItemFailures | attribute on the row |
| Streams -> Lambda `publish` | pull (event source mapping) | sync per batch | new image | stream read permissions | retries until success or the record expires; set bisect + on-failure destination SQS | read from the row |
| Lambda -> SQS settlement | push | sync SendMessage | decision (no PII) | sqs:SendMessage on one queue | SDK retries, then exception -> stream retry | message attribute |

Risks: (1) one poison message blocks every later event for that PNR until it reaches the DLQ. (2) More
moving parts (two Lambdas, a stream) for a modest volume.

Cost driver: two Lambda invocations per cancellation plus stream reads; FIFO requests cost slightly more
than Standard. Throughput is far above 50/minute.

### Comparison

| | A: SQS Standard | B: EventBridge | C: SQS FIFO + Streams |
|---|---|---|---|
| Per-PNR ordering | no | no | yes |
| Failure handling | partial batch + DLQ | async retries + destination | partial batch + DLQ, ordered |
| Settlement visibility | query the table | event feed | event feed from the table |
| Moving parts | 3 | 5 | 6 |
| Relative cost | lowest | low | low-medium |
