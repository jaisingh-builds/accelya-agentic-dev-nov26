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
    if not key.startswith(cfg.raw_prefix):
        logger.warning("file.ignored", extra={"boundary": "s3->lambda", "bucket": bucket, "key": key})
        return {"status": "ignored"}
    name = key.rsplit("/", 1)[-1]
    try:
        day = business_date(name).isoformat()
    except FileNameError:
        day = None

    try:
        raw = _call(s3.get_object, Bucket=bucket, Key=key)["Body"].read()
    except ClientError as e:
        if _code(e) in ("NoSuchKey", "404"):
            logger.warning("file.missing", extra={"boundary": "lambda->s3", "key": key, "error_type": "NoSuchKey"})
            return {"status": "missing"}
        raise
    sha = hashlib.sha256(raw).hexdigest()
    out = _Writer(s3, cfg, bucket, day or "unknown", name, cid, sha)

    previous = out.read_manifest()
    if previous and (previous.get("source_sha256"), previous.get("rules_sha256"), previous.get("ref_data_sha256")) == (
            sha, ruleset.rules_sha256, ruleset.ref_data_sha256):
        logger.info("file.already_processed", extra={"boundary": "lambda->s3", "key": key})
        return {"status": "already_processed", "manifest": previous}

    if day is None:
        return _quarantine_file(out, ruleset, logger, key, "filename", "filename", raw=raw, rows_in=0)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return _quarantine_file(out, ruleset, logger, key, "unreadable", "unreadable", raw=raw, rows_in=0)
    lines = list(csv.reader(io.StringIO(text, newline="")))
    if not lines:
        return _quarantine_file(out, ruleset, logger, key, "empty", "empty", raw=raw, rows_in=0)
    header, rows = lines[0], lines[1:]

    file_res, results = classify(business_date(name), header, [dict(zip(header, r)) for r in rows],
                                 ruleset.file_rules, ruleset.rules, ruleset.ref)
    if file_res["route"] == "quarantine":
        return _quarantine_file(out, ruleset, logger, key, file_res["reason"], file_res["rule"],
                                header=header, rows=rows)

    by_zone = defaultdict(list)
    for r in results:
        if r["route"] in ("validated", "flagged"):
            by_zone[("validated", "")].append(r)
        elif r["route"] == "quarantine":
            by_zone[("quarantine", r["reason"])].append(r)
    for (zone, reason), items in by_zone.items():
        if zone == "validated":
            body = _csv([header + ["flags", "source_line"]] +
                        [rows[r["line"] - 2] + [";".join(r["flags"]), r["line"]] for r in items])
            out.put(f"{cfg.validated_prefix}business_date={out.day}/{name}", body, "text/csv")
            logger.info("zone.written", extra={"boundary": "lambda->s3", "key": f"{cfg.validated_prefix}business_date={out.day}/{name}",
                                               "record_count": len(items)})
        else:
            folder = f"{cfg.quarantine_prefix}{reason}/business_date={out.day}/"
            out.put(folder + name, _csv([header + ["source_line"]] + [rows[r["line"] - 2] + [r["line"]] for r in items]), "text/csv")
            side = {"source_key": key, "correlation_id": cid, "reason": reason,
                    "records": [{"source_line": r["line"], "rule_id": r["rule"]} for r in items]}
            out.put(folder + name + ".sidecar.json", json.dumps(side, indent=1).encode(), "application/json")
            logger.info("zone.written", extra={"boundary": "lambda->s3", "key": folder + name, "record_count": len(items)})

    counts = Counter(r["route"] for r in results)
    manifest = _manifest(out, ruleset, key, "accepted", len(rows), counts["validated"], counts["flagged"],
                         counts["duplicate"], dict(Counter(r["reason"] for r in results if r["route"] == "quarantine")),
                         skipped=counts["skipped"])
    logger.info("file.processed", extra={"boundary": "lambda->s3", "key": key, "record_count": len(rows)})
    return {"status": "processed", "manifest": manifest}


def _code(err):
    return str(err.response.get("Error", {}).get("Code", ""))


def _call(fn, **kwargs):
    """Every S3 call goes through here: transient errors become RecoverableError, the rest propagate unchanged."""
    try:
        return fn(**kwargs)
    except ClientError as e:
        status = e.response.get("ResponseMetadata", {}).get("HTTPStatusCode") or 0
        if _code(e) in TRANSIENT_CODES or status >= 500:
            raise RecoverableError(_code(e)) from e
        raise


def _csv(table):
    buf = io.StringIO(newline="")
    csv.writer(buf).writerows(table)
    return buf.getvalue().encode("utf-8")


class _Writer:
    """Writes the output zones for one source file, each object tagged with its lineage."""

    def __init__(self, s3, cfg, bucket, day, name, cid, sha):
        self.s3, self.cfg, self.bucket, self.day, self.name, self.cid, self.sha = s3, cfg, bucket, day, name, cid, sha
        self.manifest_key = f"{cfg.manifest_prefix}business_date={day}/{name}.manifest.json"

    def put(self, key, body, content_type):
        _call(self.s3.put_object, Bucket=self.bucket, Key=key, Body=body, ContentType=content_type,
              Metadata={"correlation-id": self.cid, "source-sha256": self.sha})

    def read_manifest(self):
        try:
            return json.loads(_call(self.s3.get_object, Bucket=self.bucket, Key=self.manifest_key)["Body"].read())
        except ClientError as e:
            if _code(e) in ("NoSuchKey", "404"):
                return None
            raise


def _manifest(out, ruleset, key, file_route, rows_in, validated, flagged, duplicates, quarantined, skipped=0):
    """Written LAST: its presence marks the file as done."""
    m = {"source_key": key, "source_sha256": out.sha, "business_date": out.day, "correlation_id": out.cid,
         "rules_sha256": ruleset.rules_sha256, "ref_data_sha256": ruleset.ref_data_sha256,
         "rows_in": rows_in, "file_route": file_route, "validated": validated, "flagged": flagged,
         "duplicates": duplicates, "quarantined": quarantined}
    m["balanced"] = validated + flagged + duplicates + sum(quarantined.values()) + skipped == rows_in
    out.put(out.manifest_key, json.dumps(m, indent=1).encode(), "application/json")
    return m


def _quarantine_file(out, ruleset, logger, key, reason, file_rule, raw=None, header=None, rows=None, rows_in=None):
    """Whole-file rejection: one copy in quarantine/<reason>/, a sidecar, then the manifest."""
    folder = f"{out.cfg.quarantine_prefix}{reason}/business_date={out.day}/"
    if raw is not None:
        body = raw                                   # unreadable / no date / empty: keep the bytes as received
    else:
        body = _csv([header + ["source_line"]] + [r + [i + 2] for i, r in enumerate(rows)])
    out.put(folder + out.name, body, "text/csv")
    side = {"source_key": key, "correlation_id": out.cid, "reason": reason, "records": [{"file_rule": file_rule}]}
    out.put(folder + out.name + ".sidecar.json", json.dumps(side, indent=1).encode(), "application/json")
    n = len(rows) if rows is not None else rows_in
    manifest = _manifest(out, ruleset, key, "quarantine", n, 0, 0, 0, {reason: n})
    logger.warning("file.quarantined", extra={"boundary": "lambda->s3", "key": key, "record_count": n,
                                             "error_type": reason})
    return {"status": "quarantined", "manifest": manifest}
