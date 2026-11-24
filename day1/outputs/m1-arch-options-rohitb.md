# Architecture options (2) - rohitb

_model: us.anthropic.claude-opus-4-1-20250805-v1:0 | tokens in/out: 416/1200_

## Prompt

## Business requirement (one line, from the business)
Every time an agency uploads a daily ticket-issue file, finance must see how many tickets arrived, per airline, within 5 minutes.

## Technical prompt

**Role:** You are designing for Accelya's settlement platform on AWS (region us-east-1).

**Input:** CSV file `ticket_issue_<airline>_<date>.csv`, 5kb , columns: pnr, ticket_number, airline_code, fare_class, od_pair, issue_date, fare_amount, currency, agency_iata.

**Arrival:** 10 file per airline per agency per day, about 50 files a day, mostly between 00:00 and 02:00 UTC.

**Must:** keep the raw file unchanged for audit; show the ticket count per airline_code within 2 minutes of upload; process a re-uploaded file only once.

**Must not:** no servers to patch; never log fare_amount or PNR lists or any PII Details.

**Constraints:** serverless only; sandbox limits: max 10 Lambda functions, no public buckets.

**Output wanted:** two architecture options, answered against the six service-boundary questions for every hop.


## Bedrock answer

## Option 1: S3 → Lambda → DynamoDB → EventBridge → Lambda → SNS

**Summary:** Direct Lambda processing with DynamoDB for state management and EventBridge for scheduled aggregation.

**Services:** S3 → Lambda (Parser) → DynamoDB → EventBridge → Lambda (Aggregator) → SNS

### Service Boundaries

**S3 → Lambda (Parser)**
| Aspect | Detail |
|--------|--------|
| push or pull | Push (S3 Event) |
| sync or async | Async |
| payload shape | S3 PUT event with bucket/key reference |
| IAM role | `LambdaParserRole` with s3:GetObject, dynamodb:PutItem |
| failure path | DLQ to SQS, retry 3x with exponential backoff |
| correlation_id | S3 object ETag stored in Lambda context |

**Lambda (Parser) → DynamoDB**
| Aspect | Detail |
|--------|--------|
| push or pull | Push |
| sync or async | Sync |
| payload shape | Item: {file_hash, airline_code, ticket_count, upload_timestamp} |
| IAM role | `LambdaParserRole` with dynamodb:PutItem, dynamodb:Query |
| failure path | Lambda retry with conditional check for duplicate file_hash |
| correlation_id | file_hash (MD5 of S3 object) as partition key |

**EventBridge → Lambda (Aggregator)**
| Aspect | Detail |
|--------|--------|
| push or pull | Push (rate: 1 minute) |
| sync or async | Async |
| payload shape | Scheduled event trigger |
| IAM role | `LambdaAggregatorRole` with dynamodb:Query, sns:Publish |
| failure path | CloudWatch alarm on Lambda errors |
| correlation_id | EventBridge rule name + timestamp |

**Lambda (Aggregator) → SNS**
| Aspect | Detail |
|--------|--------|
| push or pull | Push |
| sync or async | Async |
| payload shape | {airline_code: count} aggregated for last 5 minutes |
| IAM role | `LambdaAggregatorRole` with sns:Publish |
| failure path | Retry 3x, log to CloudWatch |
| correlation_id | Message attribute with aggregation_window timestamp |

**Risks:**
1. DynamoDB hot partition if single airline dominates uploads
2. EventBridge 1-minute granularity may miss sub-minute requirement timing

## Option 2: S3 → Lambda → Kinesis Data Streams → Lambda → CloudWatch

**Summary:** Stream processing with Kinesis for real-time aggregation and CloudWatch for metrics.

**Services:** S3 → Lambda (Validator) → Kinesis Data Streams → Lambda (Processor) → CloudWatch Metrics

### Service Boundaries

**S3 → Lambda (Validator)**
| Aspect | Detail |
|--------|--------|
| push or pull | Push (S3 Event) |
| sync or async | Async |
| payload shape | S3 PUT event with bucket/key |
| IAM role | `LambdaValidatorRole` with s3:GetObject, kinesis:PutRecord |
| failure path | DLQ to SQS for manual inspection |
| correlation_id | S3 ETag passed as Kinesis partition key |

**Lambda (Validator) → Kinesis**
| Aspect | Detail |
|--------|--------|
| push or pull | Push |
| sync or async | Sync |
| payload shape | {airline_code, ticket_count, file_hash, timestamp} |
| IAM role | `LambdaValidatorRole` with kinesis:PutRecord |
| failure path | Exponential backoff, max 3 retries |
| correlation_id | file_hash as partition key |

**Kinesis → Lambda (Processor)**
| Aspect | Detail |
|--------|--------|
| push or pull | Pull (Event Source Mapping) |
| sync or async | Async |
| payload shape | Batch of records with ticket counts |
| IAM role | `LambdaProcessorRole` with kinesis:GetRecords, cloudwatch:PutMetricData |
| failure path | Bisect batch on error, checkpoint management |
| correlation_id | Kinesis sequence number |

**Lambda (Processor) → CloudWatch**
| Aspect | Detail |
|--------|--------|
| push or pull | Push |
| sync or async | Sync |
| payload shape | Metric: TicketCount, Dimension: airline_code |
| IAM role | `LambdaProcessorRole` with cloudwatch:PutMetricData |
| failure path | Retry with jitter, alert via CloudWatch Alarms |
| correlation_id | Metric timestamp + airline_code |

**Risks:**
1. Kinesis shard limits may bottleneck during 00:00
