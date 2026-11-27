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
MISSING_CODES = {"NoSuchKey", "404"}
S3_BOUNDARY = "lambda->s3"
CSV_TYPE = "text/csv"
JSON_TYPE = "application/json"
RAW_TYPE = "application/octet-stream"


class RecoverableError(Exception):
    """A retry may succeed (throttling, 5xx). args[0] is the AWS error code; the ClientError is __cause__."""

    @property
    def code(self):
        return self.args[0] if self.args else ""


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
    if not key.startswith(cfg.raw_prefix):
        logger.warning("file.ignored", extra={"key": key})
        return {"status": "ignored"}

    try:
        raw = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    except ClientError as e:
        if _aws_code(e) in MISSING_CODES:
            logger.warning("file.missing", extra={"boundary": S3_BOUNDARY, "key": key, "error_type": "NoSuchKey"})
            return {"status": "missing"}
        _raise_classified(e)

    name = key.rsplit("/", 1)[-1]
    try:
        file_date = business_date(name)
        day = file_date.isoformat()
    except FileNameError:
        file_date, day = None, "unknown"
    source_sha = hashlib.sha256(raw).hexdigest()
    manifest_key = f"{cfg.manifest_prefix}business_date={day}/{name}.manifest.json"

    stored = _read_manifest(s3, bucket, manifest_key)
    if stored and (stored.get("source_sha256"), stored.get("rules_sha256"), stored.get("ref_data_sha256")) == (
            source_sha, ruleset.rules_sha256, ruleset.ref_data_sha256):
        logger.info("file.already_processed", extra={"key": key})
        return {"status": "already_processed", "manifest": stored}

    out = _Out(s3, bucket, name, day, {"correlation-id": cid, "source-sha256": source_sha}, logger)
    parsed, reason = _parse(raw)
    if file_date is None:
        reason = "filename"
    file_rule = reason
    if parsed and not reason:
        header, rows, records = parsed
        file_res, results = classify(file_date, header, records, ruleset.file_rules, ruleset.rules, ruleset.ref)
        if file_res["route"] == "quarantine":
            reason, file_rule = file_res["reason"], file_res["rule"]
    rows_in = len(parsed[1]) if parsed else 0

    if reason:
        if parsed and reason not in ("filename", "unreadable", "empty"):
            header, rows, _ = parsed
            out.csv(cfg.quarantine_prefix + reason, header + ["source_line"],
                    [row + [i + 2] for i, row in enumerate(rows)])
        else:
            out.raw(cfg.quarantine_prefix + reason, raw)
        out.sidecar(cfg.quarantine_prefix + reason, key, cid, reason, [{"file_rule": file_rule}])
        counts, quarantined = Counter(), {reason: rows_in}
        logger.warning("file.quarantined", extra={"key": key, "record_count": rows_in, "error_code": reason})
    else:
        counts, quarantined = _write_records(out, cfg, key, cid, header, rows, results)

    manifest = {
        "source_key": key, "source_sha256": source_sha, "business_date": day, "correlation_id": cid,
        "rules_sha256": ruleset.rules_sha256, "ref_data_sha256": ruleset.ref_data_sha256,
        "rows_in": rows_in, "file_route": "quarantine" if reason else "accepted",
        "validated": counts["validated"], "flagged": counts["flagged"], "duplicates": counts["duplicate"],
        "quarantined": quarantined,
    }
    total_out = (manifest["validated"] + manifest["flagged"] + manifest["duplicates"]
                 + sum(quarantined.values()) + counts["skipped"])
    manifest["balanced"] = total_out == rows_in
    out.put(manifest_key, json.dumps(manifest, indent=1).encode("utf-8"), JSON_TYPE)   # LAST: marks the file done

    if reason:
        return {"status": "quarantined", "manifest": manifest}
    logger.info("file.processed", extra={"key": key, "record_count": rows_in})
    return {"status": "processed", "manifest": manifest}


def _write_records(out, cfg, key, cid, header, rows, results):
    """Write validated/ and quarantine/<reason>/ for an accepted file. Returns (route counts, quarantined)."""
    validated, quarantine = [], defaultdict(list)
    for r in results:
        if r["route"] in ("validated", "flagged"):
            validated.append(r)
        elif r["route"] == "quarantine":
            quarantine[r["reason"]].append(r)
    if validated:
        out.csv(cfg.validated_prefix.rstrip("/"), header + ["flags", "source_line"],
                [rows[r["line"] - 2] + [";".join(r["flags"]), r["line"]] for r in validated])
    for reason, items in quarantine.items():
        zone = cfg.quarantine_prefix + reason
        out.csv(zone, header + ["source_line"], [rows[r["line"] - 2] + [r["line"]] for r in items])
        out.sidecar(zone, key, cid, reason, [{"source_line": r["line"], "rule_id": r["rule"]} for r in items])
    return Counter(r["route"] for r in results), {k: len(v) for k, v in quarantine.items()}


class _Out:
    """Writes one file's outputs under <zone>/business_date=<day>/<name>, each with the lineage metadata."""

    def __init__(self, s3, bucket, name, day, metadata, logger):
        self.s3, self.bucket, self.name, self.day, self.metadata, self.logger = s3, bucket, name, day, metadata, logger

    def key(self, zone, suffix=""):
        return f"{zone.rstrip('/')}/business_date={self.day}/{self.name}{suffix}"

    def put(self, key, body, content_type):
        try:
            self.s3.put_object(Bucket=self.bucket, Key=key, Body=body, ContentType=content_type, Metadata=self.metadata)
        except ClientError as e:
            _raise_classified(e)

    def csv(self, zone, header, rows):
        buf = io.StringIO(newline="")
        w = csv.writer(buf)             # \r\n line endings, same as Day 3's csv.writer on a file
        w.writerow(header)
        w.writerows(rows)
        self.put(self.key(zone), buf.getvalue().encode("utf-8"), CSV_TYPE)
        self.logger.info("zone.written", extra={"boundary": S3_BOUNDARY, "key": self.key(zone), "record_count": len(rows)})

    def raw(self, zone, body):
        self.put(self.key(zone), body, RAW_TYPE)
        self.logger.info("zone.written", extra={"boundary": S3_BOUNDARY, "key": self.key(zone), "record_count": 0})

    def sidecar(self, zone, source_key, cid, reason, records):
        side = {"source_key": source_key, "correlation_id": cid, "reason": reason, "records": records}
        self.put(self.key(zone, ".sidecar.json"), json.dumps(side, indent=1).encode("utf-8"), JSON_TYPE)


def _parse(raw):
    """((header, rows, records), None) or (None, whole-file reason). rows are lists, records are dicts."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None, "unreadable"
    try:
        rows = list(csv.reader(io.StringIO(text, newline="")))
        records = list(csv.DictReader(io.StringIO(text, newline="")))
    except csv.Error:
        return None, "unreadable"
    if not rows or not rows[0]:
        return None, "empty"
    return (rows[0], rows[1:], records), None


def _read_manifest(s3, bucket, key):
    """The stored manifest, or None if this file has not been processed yet."""
    try:
        body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    except ClientError as e:
        if _aws_code(e) in MISSING_CODES:
            return None
        _raise_classified(e)
    try:
        return json.loads(body)
    except ValueError:                  # corrupt manifest: not "done", process again
        return None


def _aws_code(err):
    return err.response.get("Error", {}).get("Code", "")


def _raise_classified(err):
    """Transient (throttling, 5xx) -> RecoverableError; anything else re-raised unchanged."""
    status = err.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0)
    if _aws_code(err) in TRANSIENT_CODES or status >= 500:
        raise RecoverableError(_aws_code(err)) from err
    raise err
