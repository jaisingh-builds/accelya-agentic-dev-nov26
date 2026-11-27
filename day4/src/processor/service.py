"""Batch processing service: read one raw file, route every record, write the zones and a manifest.

No Lambda types here and no environment reads: the handler passes in the S3 client, config, rules and
logger, so every path is unit-testable with a fake S3. Error classification lives here:

  Data problem      -> NEVER raise. Route to quarantine/ (one record, or the whole file) and carry on.
  Transient AWS     -> raise RecoverableError. Lambda retries the async event twice, then the DLQ holds it.
  Anything unknown  -> let it propagate (AccessDenied, a bug). Same retry + DLQ path, for a human to read.

LAB 2: implement process_object() below with your AI assistant (prompt: day4/prompts/m4-processor-prompt.md).
Done when: python3 day4/tests/run_tests.py shows unit 13/13.
"""
import csv           # noqa: F401  (you will need these)
import hashlib       # noqa: F401
import io            # noqa: F401
import json          # noqa: F401
from collections import Counter, defaultdict   # noqa: F401
from dataclasses import dataclass

from botocore.exceptions import ClientError    # noqa: F401

from validator import FileNameError, business_date, classify, load_ref, load_rules   # noqa: F401

TRANSIENT_CODES = {"SlowDown", "InternalError", "ServiceUnavailable", "RequestTimeout",
                   "Throttling", "ThrottlingException", "RequestLimitExceeded"}


class RecoverableError(Exception):
    """A retry may succeed (throttling, 5xx)."""


@dataclass(frozen=True)
class Ruleset:
    file_rules: list
    rules: list
    rules_sha256: str
    ref: dict
    ref_data_sha256: str


def load_ruleset(cfg):
    file_rules, rules, rules_sha = load_rules(cfg.rules_path)
    ref, ref_sha = load_ref(cfg.ref_dir)
    return Ruleset(file_rules, rules, rules_sha, ref, ref_sha)


def process_object(s3, cfg, ruleset, bucket, key, cid, logger):
    """Process one raw object. Returns a dict with "status" and, when a manifest exists, "manifest".

    status  ignored            key does not start with cfg.raw_prefix       -> WARNING file.ignored, write nothing
            missing            get_object says NoSuchKey / 404              -> WARNING file.missing (error_type NoSuchKey)
            already_processed  manifest exists with the same source_sha256,
                               rules_sha256 and ref_data_sha256              -> INFO file.already_processed, write nothing
            quarantined        whole file rejected                          -> WARNING file.quarantined
            processed          records routed                               -> INFO zone.written per zone, INFO file.processed

    name = last part of key; day = validator.business_date(name).isoformat(), or "unknown" if FileNameError.
    Whole-file reasons: "filename" (no date in the name), "unreadable" (not UTF-8), "empty" (no header),
    or the header file rule from the rules file ("schema").

    Layout (same as Day 3's classify.py --zones, so the bytes match):
      validated/business_date=<day>/<name>                 header + flags + source_line; validated AND flagged rows
      quarantine/<reason>/business_date=<day>/<name>       header + source_line
      quarantine/<reason>/business_date=<day>/<name>.sidecar.json
            {"source_key", "correlation_id", "reason", "records": [{"source_line", "rule_id"}]}   (json indent=1)
            whole-file quarantine: "records": [{"file_rule": <rule or reason>}]; unreadable/filename keep the raw bytes
      manifests/business_date=<day>/<name>.manifest.json   written LAST (it marks the file as done)
            source_key, source_sha256, business_date, correlation_id, rules_sha256, ref_data_sha256, rows_in,
            file_route, validated, flagged, duplicates, quarantined {reason: count}, balanced

    Every put_object carries Metadata {"correlation-id": cid, "source-sha256": <sha256 of the raw bytes>}.
    Errors: ClientError with a code in TRANSIENT_CODES or HTTP status >= 500 -> raise RecoverableError;
            any other ClientError -> re-raise it unchanged. Data problems never raise.
    Logging: identifiers and counts only (key, record_count, error_type). Never a row or a field value.
    """
    raise NotImplementedError("Lab 2: implement process_object")
