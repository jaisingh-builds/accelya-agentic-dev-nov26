"""Powertools Logger shaped to the course logging contract (docs/LOGGING_CONTRACT.md, v1.1).

Powertools gives structured JSON, Lambda context and correlation_id handling for free, but its default
keys break the contract (timestamp, location, function_arn, a full traceback on errors). This formatter
renames or drops ONLY Powertools' own keys. Anything a developer adds with extra=... passes through
untouched, so logging a payload field still fails check_logs.py. Never hide a violation by dropping it.
"""
import time

from aws_lambda_powertools import Logger
from aws_lambda_powertools.logging.formatter import LambdaPowertoolsFormatter

# Powertools' own keys -> what the contract wants (None = drop)
_POWERTOOLS_KEYS = {
    "timestamp": None,               # replaced by "ts" (UTC, ends in Z)
    "location": None,                # file:line is noise in production logs
    "function_name": None,
    "function_arn": None,            # contains the account id
    "function_request_id": None,
    "function_memory_size": "memory_limit_mb",
    "xray_trace_id": None,
    "sampling_rate": None,
    "exception": None,               # full traceback: raw exception text is never logged
    "exception_notes": None,
    "stack_trace": None,
    "exception_name": "error_type",  # the class name is the classification
}
_FIRST = ("ts", "level", "service", "correlation_id", "boundary", "message")


class ContractFormatter(LambdaPowertoolsFormatter):
    """constant: keys every line must carry (learner_id, a default boundary). They live in the formatter, not in
    append_keys(): inject_lambda_context(clear_state=True) rebuilds Powertools' keys on every invocation and drops
    appended and constructor keys alike (verified with Powertools 3.41.0)."""

    def __init__(self, constant=None, **kwargs):
        self._constant = dict(constant or {})
        super().__init__(**kwargs)

    def format(self, record):
        self._created = record.created
        return super().format(record)

    def serialize(self, log):
        out = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(self._created)) + f".{int(self._created % 1 * 1000):03d}Z"}
        for k, v in log.items():
            new = _POWERTOOLS_KEYS.get(k, k)
            if new and v is not None and new not in out:
                out[new] = v
        if isinstance(out.get("memory_limit_mb"), str) and out["memory_limit_mb"].isdigit():
            out["memory_limit_mb"] = int(out["memory_limit_mb"])   # Lambda's context gives "256", the contract says int
        for k, v in self._constant.items():
            out.setdefault(k, v)                  # a line's own boundary wins over the default
        if "error_type" in log or "exception_name" in log:
            out.setdefault("error_type", log.get("exception_name"))
        ordered = {k: out.pop(k) for k in _FIRST if k in out}
        ordered.update(out)
        return self.json_serializer(ordered)


def build_logger(service, learner_id, level=None, handler=None):
    """One logger per function. level=None lets POWERTOOLS_LOG_LEVEL decide (INFO by default).
    handler: tests pass a capturing handler; in Lambda it is stdout, which CloudWatch collects."""
    fmt = ContractFormatter(constant={"learner_id": learner_id, "boundary": "internal"})
    return Logger(service=service, level=level, logger_formatter=fmt, logger_handler=handler)
