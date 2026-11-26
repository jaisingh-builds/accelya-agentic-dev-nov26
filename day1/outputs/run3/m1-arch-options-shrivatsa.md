# Architecture options (2) - shrivatsa

_model: us.anthropic.claude-sonnet-4-5-20250929-v1:0 | tokens in/out: 510/2875_

## Prompt

## Business requirement (one line, from the business)
Every time an agency uploads a daily ticket-issue file, finance must see how many tickets arrived, per airline, within 5 minutes.

## Technical prompt

**Role:** You are designing for Accelya's settlement platform on AWS (region us-east-1).

**Input:** CSV file `ticket_issue_<airline>_<date>.csv`, ~700 KB typical (10,000 rows), up to ~3.5 MB (50,000 rows) at peak, columns: pnr, ticket_number, airline_code, fare_class, od_pair, issue_date, fare_amount, currency, agency_iata.

**Arrival:** each agency uploads one file per airline per day, ~200 files per day, mostly in a burst of about an hour after the daily cut-off; every file's count must be visible within 5 minutes, even at peak.

**Must:** the original file is kept unchanged for audit, and failed files are stored separately for audit; finance sees a running ticket count per airline per day on a dashboard within 5 minutes of upload; a malformed file triggers a notification and does not block other files.

**Must not:** the finance view shows only the ticket count per airline, with no pnr, fare_amount, fare_class or od_pair; no servers to patch and no database we manage ourselves (a managed serverless one is fine), and keep the pipeline simple.

**Constraints:** serverless only; sandbox limits: max 10 Lambda functions, no public buckets.

**Output wanted:** two architecture options, answered against the six service-boundary questions for every hop.


## Bedrock answer

# Option 1: S3 → Lambda → DynamoDB → API Gateway → CloudFront

**Summary:** Event-driven pipeline with Lambda parsing CSV on upload, aggregating counts into DynamoDB, served via API Gateway to a CloudFront-hosted dashboard.

**Services in order:**
1. S3 (landing bucket for uploads)
2. Lambda (CSV parser & aggregator)
3. DynamoDB (ticket count table)
4. S3 (failed files bucket)
5. SNS (failure notifications)
6. API Gateway (REST API for dashboard)
7. S3 (dashboard static site)
8. CloudFront (CDN for dashboard)

## Service Boundary Analysis

### Hop 1: S3 → Lambda (CSV Parser)
| Question | Answer |
|----------|--------|
| **Push or Pull** | Push (S3 Event Notification) |
| **Sync or Async** | Async |
| **Payload shape** | S3 event JSON with bucket/key; Lambda reads CSV from S3 |
| **IAM role** | Lambda execution role with `s3:GetObject` on landing bucket |
| **Failure path** | Lambda DLQ → SNS; original file remains in S3 |
| **correlation_id** | S3 object key (contains airline + date); stored in Lambda logs and DynamoDB item |

### Hop 2: Lambda → DynamoDB
| Question | Answer |
|----------|--------|
| **Push or Pull** | Push (Lambda writes) |
| **Sync or Async** | Sync (with retries) |
| **Payload shape** | `{pk: "AIRLINE#AA", sk: "DATE#2025-01-15", ticket_count: 8543, file_key: "...", processed_at: ISO8601}` |
| **IAM role** | Lambda execution role with `dynamodb:PutItem`, `dynamodb:UpdateItem` |
| **Failure path** | Lambda retries 2x, then DLQ → SNS |
| **correlation_id** | File key embedded in DynamoDB item metadata attribute |

### Hop 3: Lambda → S3 (Failed Files)
| Question | Answer |
|----------|--------|
| **Push or Pull** | Push (Lambda copies on parse error) |
| **Sync or Async** | Sync |
| **Payload shape** | Original CSV file + `.error.json` metadata file with error details |
| **IAM role** | Lambda execution role with `s3:PutObject` on failed-files bucket |
| **Failure path** | Logged to CloudWatch; SNS alert sent regardless |
| **correlation_id** | Original S3 key preserved; error JSON includes correlation_id field |

### Hop 4: Lambda → SNS
| Question | Answer |
|----------|--------|
| **Push or Pull** | Push |
| **Sync or Async** | Async |
| **Payload shape** | `{error_type: "MALFORMED_CSV", file_key: "...", airline: "AA", row_number: 342, correlation_id: "..."}` |
| **IAM role** | Lambda execution role with `sns:Publish` |
| **Failure path** | Logged to CloudWatch; does not block file processing |
| **correlation_id** | Included in SNS message body |

### Hop 5: API Gateway → DynamoDB
| Question | Answer |
|----------|--------|
| **Push or Pull** | Pull (API Gateway queries via Lambda or direct integration) |
| **Sync or Async** | Sync |
| **Payload shape** | Query: `GET /counts?airline=AA&date=2025-01-15`; Response: `{airline: "AA", date: "2025-01-15", ticket_count: 8543}` |
| **IAM role** | API Gateway execution role with `dynamodb:Query` (direct integration) or Lambda role |
| **Failure path** | 5xx error to dashboard; CloudWatch alarm on error rate |
| **correlation_id** | Passed as `x-correlation-id` header from dashboard request |

### Hop 6: CloudFront → API Gateway
| Question | Answer |
|----------|--------|
| **Push or Pull** | Pull (dashboard polls API every 30s) |
| **Sync or Async** | Sync |
| **Payload shape** | HTTPS GET request; JSON response array of airline counts |
| **IAM role** | None (public API with WAF rules) |
| **Failure path** | Dashboard shows cached data or "unavailable" message |
| **correlation_id** | Generated in browser, sent with each API call |

**Risks:**
1. **DynamoDB hot partition:** All agencies uploading same airline simultaneously could throttle writes (mitigate: use airline+date+agency as sort key).
2. **Lambda concurrency:** 200 files in 1 hour = potential cold starts and throttling (mitigate: reserved concurrency of 50).

---

# Option 2: S3 → EventBridge → Step Functions → Athena → QuickSight

**Summary:** Orchestrated pipeline using Step Functions to coordinate parsing, S3-based data lake queried by Athena, visualized in QuickSight with auto-refresh.

**Services in order:**
1. S3 (landing bucket)
2. EventBridge (event bus)
3. Step Functions (orchestration)
4. Lambda (CSV validator & counter)
5. S3 (processed data bucket, partitioned by airline/date)
6. S3 (failed files bucket)
7. SNS (notifications)
8. Athena (query engine)
9. QuickSight (dashboard)

## Service Boundary Analysis

### Hop 1: S3 → EventBridge
| Question | Answer |
|----------|--------|
| **Push or Pull** | Push (S3 Event Notifications to EventBridge) |
| **Sync or Async** | Async |
| **Payload shape** | EventBridge event with `detail.bucket.name`, `detail.object.key` |
| **IAM role** | S3 service role with `events:PutEvents` |
| **Failure path** | EventBridge DLQ; original file remains in S3 |
| **correlation_id** | Generated by EventBridge as `detail.eventId`; propagated through all steps |

### Hop 2: EventBridge → Step Functions
| Question | Answer |
|----------|--------|
| **Push or Pull** | Push (EventBridge rule triggers execution) |
| **Sync or Async** | Async |
| **Payload shape** | Step Functions input: `{file_key: "...", airline: "AA", date: "2025-01-15", correlation_id: "..."}` |
| **IAM role** | EventBridge role with `states:StartExecution` |
| **Failure path** | EventBridge DLQ; CloudWatch alarm on failed rule invocations |
| **correlation_id** | Passed as top-level field in execution input |

### Hop 3: Step Functions → Lambda (Validator)
| Question | Answer |
|----------|--------|
| **Push or Pull** | Push (Step Functions invokes Lambda task) |
| **Sync or Async** | Sync (Step Functions waits for response) |
| **Payload shape** | Input: file metadata; Output: `{valid: true, ticket_count: 8543, row_errors: []}` or `{valid: false, errors: [...]}` |
| **IAM role** | Step Functions role with `lambda:InvokeFunction`; Lambda role with `s3:GetObject` |
| **Failure path** | Step Functions catches error, transitions to failure branch (copy to failed bucket + SNS) |
| **correlation_id** | Passed in Lambda event, returned in response, logged to CloudWatch |

### Hop 4: Lambda → S3 (Processed Data)
| Question | Answer |
|----------|--------|
| **Push or Pull** | Push (Lambda writes aggregated JSON) |
| **Sync or Async** | Sync |
| **Payload shape** | JSON: `{airline: "AA", date: "2025-01-15", ticket_count: 8543, file_key: "...", processed_at: "..."}` partitioned as `s3://.../airline=AA/date=2025-01-15/counts.json` |
| **IAM role** | Lambda execution role with `s3:PutObject` |
| **Failure path** | Lambda returns error to Step Functions; Step Functions retries 2x then fails |
| **correlation_id** | Embedded in JSON payload as metadata field |

### Hop 5: Lambda → S3 (Failed Files) + SNS
| Question | Answer |
|----------|--------|
| **Push or Pull** | Push (Step Functions failure branch invokes Lambda) |
| **Sync or Async** | Sync (S3 copy), Async (SNS) |
| **Payload shape** | Original CSV + error manifest JSON; SNS message with failure details |
| **IAM role** | Lambda role with `s3:CopyObject`, `sns:Publish` |
| **Failure path** | Logged to CloudWatch; Step Functions execution marked failed |
| **correlation_id** | Included in error manifest and SNS message |

### Hop 6: Athena → S3 (Processed Data)
| Question | Answer |
|----------|--------|
| **Push or Pull** | Pull (Athena queries on-demand or scheduled) |
| **Sync or Async** | Sync (query execution) |
| **Payload shape** | SQL: `SELECT airline, date, SUM(ticket_count) FROM counts WHERE date = CURRENT_DATE GROUP BY airline, date` |
| **IAM role** | Athena execution role with `s3:GetObject`, `s3:ListBucket` on processed bucket |
| **Failure path** | Query fails; QuickSight shows stale data or error |
| **correlation_id** | Not directly tracked; query execution ID serves as trace |

### Hop 7: QuickSight → Athena
| Question | Answer |
|----------|--------|
| **Push or Pull** | Pull (QuickSight refreshes dataset every 5 min via SPICE or direct query) |
| **Sync or Async** | Sync |
| **Payload shape** | Athena result set: rows of `{airline, date, ticket_count}` |
| **IAM role** | QuickSight service role with `athena:StartQueryExecution`, `athena:GetQueryResults` |
| **Failure path** | Dashboard shows "Data unavailable"; CloudWatch alarm on refresh failures |
| **correlation_id** | Not applicable (aggregated view) |

**Risks:**
1. **Athena query latency:** Complex queries or large partitions could exceed 5-minute SLA (mitigate: pre-aggregate in Lambda, use SPICE).
2. **Step Functions cost:** 200 executions/day with multiple states could accumulate cost (mitigate: batch multiple files per execution if timing allows).

---

# Comparison

**Option 1** is simpler and faster: direct Lambda processing, DynamoDB provides sub-second reads, custom dashboard polls API. Lower latency (typically <1 min), easier debugging, lower cost (~$15/month). Best for real-time requirements.

**Option 2** is more auditable and scalable: Step Functions provides visual workflow, S3 data lake enables ad-hoc analysis, QuickSight offers richer BI features. Higher latency (2-5 min), better for compliance-heavy environments, higher cost (~$50/month with QuickSight). Best if finance needs drill-down beyond counts.

**Recommendation:** Option 1 for the stated requirement (simple count dashboard, 5-minute SLA). Option 2 if future analytics needs are anticipated.
