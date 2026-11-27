"""Batch processing service: read one raw file, route every record, write the zones and a manifest.

No Lambda types here and no environment reads: the handler passes in the S3 client, config, rules and
logger, so every path is unit-testable with a fake S3. Error classification lives here:

  Data problem      -> NEVER raise. Route to quarantine/ (one record, or the whole file) and carry on.
  Transient AWS     -> raise RecoverableError. Lambda retries the async event twice, then the DLQ holds it.
  Anything unknown  -> let it propagate (AccessDenied, a bug). Same retry + DLQ path, for a human to read.

LAB 2: implement process_object() below with your AI assistant (prompt: day4/prompts/m4-processor-prompt.md).
Done when: python3 day4/tests/run_tests.py shows unit 13/13.
"""
import csv
import hashlib
import io
import json
from collections import Counter, defaultdict
from dataclasses import dataclass

from botocore.exceptions import ClientError

from validator import FileNameError, business_date, classify, load_ref, load_rules

TRANSIENT_CODES = {"SlowDown", "InternalError", "ServiceUnavailable", "RequestTimeout",
                   "Throttling", "ThrottlingException", "RequestLimitExceeded"}
NOT_FOUND_CODES = {"NoSuchKey", "404", "NotFound"}
RAW_REASONS = {"filename", "unreadable", "empty"}   # whole-file reasons where the rows cannot be trusted: keep the raw bytes


class RecoverableError(Exception):
    """A retry may succeed (throttling, 5xx). `code` is the AWS error code, so the handler can log it."""

    def __init__(self, code=""):
        super().__init__(code)
        self.code = code


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


# ---------------------------------------------------------------- small helpers
def _code(err):
    return err.response.get("Error", {}).get("Code", "")


def _http(err):
    return err.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0)


def _call(fn, **kwargs):
    """Call one S3 operation. A transient ClientError becomes RecoverableError; any other one is re-raised unchanged."""
    try:
        return fn(**kwargs)
    except ClientError as err:
        if _code(err) in TRANSIENT_CODES or _http(err) >= 500:
            raise RecoverableError(_code(err)) from err
        raise


def _read(s3, bucket, key):
    """Return the object's bytes, or None when the object does not exist."""
    try:
        return _call(s3.get_object, Bucket=bucket, Key=key)["Body"].read()
    except ClientError as err:
        if _code(err) in NOT_FOUND_CODES or _http(err) == 404:
            return None
        raise


def _stored_manifest(s3, bucket, key):
    """The manifest from an earlier run, or None (missing, or not readable JSON: then we simply process again)."""
    raw = _read(s3, bucket, key)
    if raw is None:
        return None
    try:
        stored = json.loads(raw)
    except ValueError:
        return None
    return stored if isinstance(stored, dict) else None


def _csv_bytes(rows):
    buf = io.StringIO()
    csv.writer(buf).writerows(rows)          # same writer and line endings as Day 3's classify.py --zones
    return buf.getvalue().encode("utf-8")


def _parse(text):
    """(header, data rows) as lists. Blank lines are skipped, as csv.DictReader does in Day 3."""
    rows = [r for r in csv.reader(io.StringIO(text, newline="")) if r]
    return (rows[0], rows[1:]) if rows else ([], [])


def _sidecar(key, cid, reason, records):
    side = {"source_key": key, "correlation_id": cid, "reason": reason, "records": records}
    return json.dumps(side, indent=1).encode("utf-8")


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
    if not key.startswith(cfg.raw_prefix):
        logger.warning("file.ignored", extra={"key": key})
        return {"status": "ignored"}

    name = key.rsplit("/", 1)[-1]
    try:
        file_date = business_date(name)
        day = file_date.isoformat()
    except FileNameError:
        file_date, day = None, "unknown"

    body = _read(s3, bucket, key)
    if body is None:
        logger.warning("file.missing", extra={"key": key, "error_type": "NoSuchKey"})
        return {"status": "missing"}
    source_sha = hashlib.sha256(body).hexdigest()

    manifest_key = f"{cfg.manifest_prefix}business_date={day}/{name}.manifest.json"
    stored = _stored_manifest(s3, bucket, manifest_key)
    if stored and (stored.get("source_sha256"), stored.get("rules_sha256"), stored.get("ref_data_sha256")) == \
            (source_sha, ruleset.rules_sha256, ruleset.ref_data_sha256):
        logger.info("file.already_processed", extra={"key": key})
        return {"status": "already_processed", "manifest": stored}

    # ---- decode, parse, classify: every data problem ends up as a reason, never an exception
    header, rows, results, reason, rule = [], [], [], "", ""
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        text = None
    if text is not None:
        try:
            header, rows = _parse(text)
        except csv.Error:
            text = None
    if file_date is None:
        reason = "filename"
    elif text is None:
        reason = "unreadable"
    elif not header:
        reason = "empty"
    else:
        dicts = [dict(zip(header, r)) for r in rows]
        file_res, results = classify(file_date, header, dicts, ruleset.file_rules, ruleset.rules, ruleset.ref)
        if file_res["route"] == "quarantine":
            reason, rule = file_res["reason"], file_res["rule"]

    # ---- build every output first, so a bad record can never leave half the zones written
    outputs = []          # (key, bytes, content type, record_count, zone_name)
    quarantine_dir = f"{cfg.quarantine_prefix}%s/business_date={day}/{name}"
    if reason:
        out_key = quarantine_dir % reason
        if reason in RAW_REASONS:
            data = body
        else:
            data = _csv_bytes([header + ["source_line"]] + [r + [i + 2] for i, r in enumerate(rows)])
        outputs.append((out_key, data, "text/csv", len(rows), "quarantine"))
        outputs.append((out_key + ".sidecar.json", _sidecar(key, cid, reason, [{"file_rule": rule or reason}]),
                        "application/json", 1, "quarantine"))
    else:
        kept = [r for r in results if r["route"] in ("validated", "flagged")]
        if kept:
            data = _csv_bytes([header + ["flags", "source_line"]]
                              + [rows[r["line"] - 2] + [";".join(r["flags"]), r["line"]] for r in kept])
            outputs.append((f"{cfg.validated_prefix}business_date={day}/{name}", data, "text/csv", len(kept), "validated"))
        by_reason = defaultdict(list)
        for r in results:
            if r["route"] == "quarantine":
                by_reason[r["reason"]].append(r)
        for why, items in by_reason.items():
            out_key = quarantine_dir % why
            data = _csv_bytes([header + ["source_line"]] + [rows[r["line"] - 2] + [r["line"]] for r in items])
            records = [{"source_line": r["line"], "rule_id": r["rule"]} for r in items]
            outputs.append((out_key, data, "text/csv", len(items), "quarantine"))
            outputs.append((out_key + ".sidecar.json", _sidecar(key, cid, why, records),
                            "application/json", len(items), "quarantine"))

    counts = Counter(r["route"] for r in results)
    quarantined = {reason: len(rows)} if reason else dict(Counter(r["reason"] for r in results if r["route"] == "quarantine"))
    accounted = (counts["validated"] + counts["flagged"] + counts["duplicate"] + counts["skipped"]
                 + sum(quarantined.values()))
    manifest = {
        "source_key": key, "source_sha256": source_sha, "business_date": day, "correlation_id": cid,
        "rules_sha256": ruleset.rules_sha256, "ref_data_sha256": ruleset.ref_data_sha256,
        "rows_in": len(rows), "file_route": "quarantine" if reason else "accepted",
        "validated": counts["validated"], "flagged": counts["flagged"], "duplicates": counts["duplicate"],
        "quarantined": quarantined, "balanced": accounted == len(rows),
    }

    # ---- write: zones first, the manifest LAST (it marks the file as done, so a crash means a clean retry)
    meta = {"correlation-id": cid, "source-sha256": source_sha}
    for out_key, data, ctype, count, zone in outputs:
        _call(s3.put_object, Bucket=bucket, Key=out_key, Body=data, ContentType=ctype, Metadata=meta)
        if not reason and not out_key.endswith(".sidecar.json"):
            logger.info("zone.written", extra={"key": out_key, "record_count": count, "boundary": "lambda->s3"})
    _call(s3.put_object, Bucket=bucket, Key=manifest_key, Body=json.dumps(manifest, indent=1).encode("utf-8"),
          ContentType="application/json", Metadata=meta)

    if reason:
        logger.warning("file.quarantined", extra={"key": key, "record_count": len(rows)})
        return {"status": "quarantined", "manifest": manifest}
    logger.info("file.processed", extra={"key": key, "record_count": len(rows)})
    return {"status": "processed", "manifest": manifest}
