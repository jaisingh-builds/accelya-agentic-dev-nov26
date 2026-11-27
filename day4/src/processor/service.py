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
        logger.warning("file.ignored", extra={"bucket": bucket, "key": key})
        return {"status": "ignored"}

    raw = _read_object(s3, bucket, key, logger)
    if raw is None:
        return {"status": "missing"}

    name = key.rsplit("/", 1)[-1]
    try:
        file_date = business_date(name)
        day = file_date.isoformat()
    except FileNameError:
        file_date, day = None, "unknown"
    sha = hashlib.sha256(raw).hexdigest()
    meta = {"correlation-id": cid, "source-sha256": sha}
    manifest_key = f"{cfg.manifest_prefix}business_date={day}/{name}.manifest.json"

    prev = _previous_manifest(s3, bucket, manifest_key)
    if prev and (prev.get("source_sha256"), prev.get("rules_sha256"), prev.get("ref_data_sha256")) == \
            (sha, ruleset.rules_sha256, ruleset.ref_data_sha256):
        logger.info("file.already_processed", extra={"bucket": bucket, "key": key})
        return {"status": "already_processed", "manifest": prev}

    # ---- read the file; any whole-file problem is a data problem, so it is quarantined, never raised
    reason, header, rows = None, [], []
    if file_date is None:
        reason = "filename"
    try:
        parsed = [r for r in csv.reader(io.StringIO(raw.decode("utf-8"), newline="")) if r]
    except (UnicodeDecodeError, csv.Error):
        parsed, reason = [], reason or "unreadable"
    if parsed:
        header, rows = parsed[0], parsed[1:]
    elif reason is None:
        reason = "empty"

    zone_files = []                       # (key, body, record_count) in write order
    counts, quarantined = Counter(), {}
    if reason:                            # keep the raw bytes, one sidecar
        base = f"{cfg.quarantine_prefix}{reason}/business_date={day}/{name}"
        zone_files += [(base, raw, len(rows)),
                       (base + ".sidecar.json", _sidecar(key, cid, reason, [{"file_rule": reason}]), len(rows))]
        file_route, quarantined = "quarantine", {reason: len(rows)}
    else:
        dict_rows = [dict(zip(header, r)) for r in rows]
        file_res, results = classify(file_date, header, dict_rows, ruleset.file_rules, ruleset.rules, ruleset.ref)
        if file_res["route"] == "quarantine":            # the header file rule: whole file
            why = file_res["reason"]
            base = f"{cfg.quarantine_prefix}{why}/business_date={day}/{name}"
            body = _csv_bytes(header + ["source_line"], [r + [i + 2] for i, r in enumerate(rows)])
            records = [{"file_rule": file_res.get("rule", why)}]
            zone_files += [(base, body, len(rows)), (base + ".sidecar.json", _sidecar(key, cid, why, records), len(rows))]
            file_route, quarantined = "quarantine", {why: len(rows)}
        else:
            file_route = "accepted"
            counts = Counter(x["route"] for x in results)
            ok = [x for x in results if x["route"] in ("validated", "flagged")]
            if ok:
                body = _csv_bytes(header + ["flags", "source_line"],
                                  [rows[x["line"] - 2] + [";".join(x["flags"]), x["line"]] for x in ok])
                zone_files.append((f"{cfg.validated_prefix}business_date={day}/{name}", body, len(ok)))
            by_reason = defaultdict(list)
            for x in results:
                if x["route"] == "quarantine":
                    by_reason[x["reason"]].append(x)
            for why, items in by_reason.items():
                base = f"{cfg.quarantine_prefix}{why}/business_date={day}/{name}"
                body = _csv_bytes(header + ["source_line"], [rows[x["line"] - 2] + [x["line"]] for x in items])
                records = [{"source_line": x["line"], "rule_id": x["rule"]} for x in items]
                zone_files += [(base, body, len(items)), (base + ".sidecar.json", _sidecar(key, cid, why, records), len(items))]
            quarantined = {why: len(items) for why, items in by_reason.items()}

    for out_key, body, n in zone_files:
        _put(s3, bucket, out_key, body, meta)
        logger.info("zone.written", extra={"boundary": "lambda->s3", "bucket": bucket, "key": out_key,
                                           "record_count": n, "bytes": len(body)})

    manifest = {
        "source_key": key, "source_sha256": sha, "business_date": day, "correlation_id": cid,
        "rules_sha256": ruleset.rules_sha256, "ref_data_sha256": ruleset.ref_data_sha256,
        "rows_in": len(rows), "file_route": file_route,
        "validated": counts["validated"], "flagged": counts["flagged"], "duplicates": counts["duplicate"],
        "quarantined": quarantined,
    }
    manifest["balanced"] = (manifest["validated"] + manifest["flagged"] + manifest["duplicates"]
                            + counts["skipped"] + sum(quarantined.values())) == len(rows)
    _put(s3, bucket, manifest_key, json.dumps(manifest, indent=1).encode("utf-8"), meta)   # LAST

    if file_route == "quarantine":
        logger.warning("file.quarantined", extra={"bucket": bucket, "key": key, "record_count": len(rows),
                                                  "error_code": next(iter(quarantined))})
        return {"status": "quarantined", "manifest": manifest}
    logger.info("file.processed", extra={"bucket": bucket, "key": key, "record_count": len(rows)})
    return {"status": "processed", "manifest": manifest}


# ---- helpers (S3 calls, error classification, output bytes)

def _csv_bytes(columns, rows):
    buf = io.StringIO(newline="")
    writer = csv.writer(buf)
    writer.writerow(columns)
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8")


def _sidecar(key, cid, reason, records):
    side = {"source_key": key, "correlation_id": cid, "reason": reason, "records": records}
    return json.dumps(side, indent=1).encode("utf-8")


def _call(operation, **kwargs):
    """Run one S3 call. A transient ClientError becomes RecoverableError; any other one is re-raised unchanged."""
    try:
        return operation(**kwargs)
    except ClientError as e:
        error = e.response.get("Error", {})
        status = e.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0)
        if error.get("Code") in TRANSIENT_CODES or status >= 500:
            code = error.get("Code") or f"HTTP{status}"
            retry = RecoverableError(code)
            retry.code = code                 # the handler logs this as error_code
            raise retry from e
        raise


def _is_missing(e):
    return e.response.get("Error", {}).get("Code") in ("NoSuchKey", "404", "NotFound")


def _read_object(s3, bucket, key, logger):
    """The object's bytes, or None (with a WARNING) when it no longer exists."""
    try:
        return _call(s3.get_object, Bucket=bucket, Key=key)["Body"].read()
    except ClientError as e:
        if not _is_missing(e):
            raise
        logger.warning("file.missing", extra={"bucket": bucket, "key": key, "error_type": "NoSuchKey"})
        return None


def _previous_manifest(s3, bucket, manifest_key):
    """The stored manifest of an earlier run, or None. A damaged manifest counts as 'not done'."""
    try:
        body = _call(s3.get_object, Bucket=bucket, Key=manifest_key)["Body"].read()
    except ClientError as e:
        if not _is_missing(e):
            raise
        return None
    try:
        return json.loads(body)
    except ValueError:
        return None


def _put(s3, bucket, key, body, meta):
    ctype = "text/csv" if key.endswith(".csv") else "application/json" if key.endswith(".json") else "application/octet-stream"
    _call(s3.put_object, Bucket=bucket, Key=key, Body=body, ContentType=ctype, Metadata=meta)
