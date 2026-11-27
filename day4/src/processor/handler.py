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
    for record in event.get("Records", []):
        bucket = record["s3"]["bucket"]["name"]
        key = unquote_plus(record["s3"]["object"]["key"])
        try:
            head = s3.head_object(Bucket=bucket, Key=key)
            size, cid = head.get("ContentLength"), (head.get("Metadata") or {}).get("correlation-id")
        except ClientError:
            size, cid = None, None        # process_object finds and classifies the real problem
        minted = not (cid and CID_OK.fullmatch(cid))
        if minted:
            cid = "m4-" + context.aws_request_id[:12]
        logger.set_correlation_id(cid)
        if minted:
            logger.warning("metadata.missing", extra={"bucket": bucket, "key": key})
        received = {"boundary": "s3->lambda", "bucket": bucket, "key": key}
        if size is not None:
            received["bytes"] = size
        logger.info("file.received", extra=received)
        try:
            result = process_object(s3, CFG, _rules(), bucket, key, cid, logger)
        except RecoverableError as e:
            logger.error("file.retry", extra={"error_type": "RecoverableError", "error_code": getattr(e, "code", "unknown")})
            raise
        except Exception as e:
            logger.error("file.failed", extra={"error_type": type(e).__name__})
            raise
        statuses.append(result["status"])
    return {"files": len(statuses), "statuses": statuses}
