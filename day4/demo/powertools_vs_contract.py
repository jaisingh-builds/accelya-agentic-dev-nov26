#!/usr/bin/env python3
"""Demo 2: the same three log calls, first with Powertools' default output, then shaped to the contract.

    python3 day4/demo/powertools_vs_contract.py default  | python3 day2/scripts/check_logs.py -
    python3 day4/demo/powertools_vs_contract.py contract | python3 day2/scripts/check_logs.py -
    python3 day4/demo/powertools_vs_contract.py leak     | python3 day2/scripts/check_logs.py -   # a payload key: not hidden
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "processor"))
from aws_lambda_powertools import Logger   # noqa: E402

from log import ContractFormatter          # noqa: E402


class Ctx:                                  # what Lambda passes as `context`
    function_name = "accelya-m4-demo-processor"
    memory_limit_in_mb = "256"      # Lambda passes a string
    invoked_function_arn = "arn:aws:lambda:us-east-1:111122223333:function:accelya-m4-demo-processor"
    aws_request_id = "4f1c2e9a-0b7d-4c3e-9a51-6d2f8b0e7c11"


mode = sys.argv[1] if len(sys.argv) > 1 else "default"
logger = Logger(service="accelya-m4-demo-processor",
                logger_formatter=ContractFormatter() if mode in ("contract", "leak") else None)


@logger.inject_lambda_context
def handler(event, context):
    logger.set_correlation_id("demo-d4-001")
    logger.info("file.received", extra={"boundary": "s3->lambda", "key": "raw/ticket_issue_AI_2026-11-25.csv", "bytes": 2964})
    extra = {"boundary": "lambda->s3", "key": "validated/business_date=2026-11-25/ticket_issue_AI_2026-11-25.csv", "record_count": 22}
    if mode == "leak":
        extra["fare_amount"] = "6440.10"            # a payload field: the formatter must NOT hide it
    logger.info("zone.written", extra=extra)
    try:
        raise PermissionError("Access Denied for arn:aws:s3:::accelya-m4-demo-111122223333/raw/ticket_issue_AI_2026-11-25.csv")
    except PermissionError:
        logger.exception("file.failed", extra={"boundary": "lambda->s3"})


handler({}, Ctx())
