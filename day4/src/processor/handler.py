"""Lambda entry point. Thin on purpose: unpack the S3 event, set the correlation_id, call the service,
log the outcome and let failures surface. All business logic is in service.py / validator.py.

LAB 3: finish handler() with your AI assistant (prompt: day4/prompts/m4-processor-prompt.md, part B).
Done when: python3 day4/tests/run_tests.py shows logging 6/6.
"""
import re
from urllib.parse import unquote_plus     # noqa: F401  (S3 event keys are URL-encoded)

import boto3
from botocore.exceptions import ClientError    # noqa: F401

from config import load_config
from log import build_logger
from service import RecoverableError, load_ruleset, process_object   # noqa: F401

CFG = load_config()
logger = build_logger(CFG.service, CFG.learner_id)
s3 = boto3.client("s3")
CID_OK = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")   # docs/logging-contract.json, correlation_id
_ruleset = None


def _rules():
    """Load the rules once per container (cold start), not once per file."""
    global _ruleset
    if _ruleset is None:
        _ruleset = load_ruleset(CFG)
        logger.debug("rules.loaded", extra={"record_count": len(_ruleset.rules)})
    return _ruleset


def _correlation_id(context, meta):
    """The id the uploader set, if it is valid; otherwise a minted one (and True, so the caller can warn)."""
    cid = (meta or {}).get("correlation-id", "")
    return (cid, False) if CID_OK.match(cid) else ("m4-" + context.aws_request_id[:12], True)


@logger.inject_lambda_context(clear_state=True)
def handler(event, context):
    """For each S3 record in the event:
      1. bucket and key (unquote_plus the key)
      2. correlation_id = the object's x-amz-meta-correlation-id (head_object Metadata "correlation-id") if it
         matches CID_OK; otherwise mint "m4-" + context.aws_request_id[:12] and log WARNING metadata.missing
      3. logger.set_correlation_id(cid), then INFO file.received (boundary s3->lambda, bucket, key, bytes)
      4. process_object(s3, CFG, _rules(), bucket, key, cid, logger)
      5. RecoverableError -> ERROR file.retry (error_type "RecoverableError", error_code = the AWS code) and re-raise
         any other Exception -> ERROR file.failed (error_type = the exception class name) and re-raise
    Return {"files": <count>, "statuses": [<status per file>]}.
    Decorate the handler with Powertools' inject_lambda_context(clear_state=True) so every line carries
    cold_start and memory_limit_mb, and no key leaks from one invocation into the next.
    """
    statuses = []
    for rec in event["Records"]:
        bucket = rec["s3"]["bucket"]["name"]
        key = unquote_plus(rec["s3"]["object"]["key"])
        try:
            head = s3.head_object(Bucket=bucket, Key=key)
        except ClientError:
            head = {}                      # the object is gone or unreadable: process_object classifies it below
        cid, minted = _correlation_id(context, head.get("Metadata"))
        logger.set_correlation_id(cid)     # before the first line of this record, so every line carries it
        if minted:
            logger.warning("metadata.missing", extra={"key": key})
        received = {"boundary": "s3->lambda", "bucket": bucket, "key": key}
        if "ContentLength" in head:
            received["bytes"] = head["ContentLength"]
        logger.info("file.received", extra=received)
        try:
            result = process_object(s3, CFG, _rules(), bucket, key, cid, logger)
        except RecoverableError as err:
            logger.error("file.retry", extra={"error_type": "RecoverableError", "error_code": err.code})
            raise
        except Exception as err:            # noqa: BLE001  (log the class only, then let Lambda retry / DLQ)
            logger.error("file.failed", extra={"error_type": type(err).__name__})
            raise
        statuses.append(result["status"])
    return {"files": len(statuses), "statuses": statuses}
