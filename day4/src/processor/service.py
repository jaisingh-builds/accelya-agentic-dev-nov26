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
import os
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


def _classify_client_error(exc):
    """Re-raise as RecoverableError for transient codes / HTTP 5xx; re-raise original otherwise."""
    code = exc.response.get("Error", {}).get("Code", "")
    status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0)
    if code in TRANSIENT_CODES or status >= 500:
        raise RecoverableError(str(code)) from exc
    raise


def _put(s3, bucket, key, body, cid, source_sha256):
    """put_object with lineage metadata; classifies ClientErrors."""
    try:
        s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=body,
            Metadata={"correlation-id": cid, "source-sha256": source_sha256},
        )
    except ClientError as exc:
        _classify_client_error(exc)


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
    # ---- step 1: ignore keys outside the raw prefix
    if not key.startswith(cfg.raw_prefix):
        logger.warning("file.ignored", extra={"boundary": "s3->lambda", "key": key})
        return {"status": "ignored"}

    name = key.split("/")[-1]

    # ---- step 2: fetch the raw bytes
    try:
        resp = s3.get_object(Bucket=bucket, Key=key)
        raw_bytes = resp["Body"].read()
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0)
        if code in ("NoSuchKey", "404") or status == 404:
            logger.warning("file.missing", extra={"boundary": "s3->lambda", "key": key, "error_type": "NoSuchKey"})
            return {"status": "missing"}
        _classify_client_error(exc)

    source_sha256 = hashlib.sha256(raw_bytes).hexdigest()

    # ---- step 3: determine business date (may be "unknown" on FileNameError)
    try:
        day = business_date(name).isoformat()
    except FileNameError:
        day = "unknown"

    # ---- step 4: idempotency check via manifest
    manifest_key = f"{cfg.manifest_prefix}business_date={day}/{name}.manifest.json"
    try:
        mresp = s3.head_object(Bucket=bucket, Key=manifest_key)
        existing_meta = mresp.get("Metadata", {})
        # Re-read the manifest body to compare hashes
        mobj = s3.get_object(Bucket=bucket, Key=manifest_key)
        existing_manifest = json.loads(mobj["Body"].read())
        if (existing_manifest.get("source_sha256") == source_sha256
                and existing_manifest.get("rules_sha256") == ruleset.rules_sha256
                and existing_manifest.get("ref_data_sha256") == ruleset.ref_data_sha256):
            logger.info("file.already_processed", extra={"boundary": "s3->lambda", "key": key})
            return {"status": "already_processed", "manifest": existing_manifest}
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0)
        if code not in ("NoSuchKey", "404", "404") and status not in (404,):
            _classify_client_error(exc)
        # manifest does not exist yet -> proceed

    # ---- step 5: data-quality checks before parsing

    # filename check (no business date)
    if day == "unknown":
        q_key = f"{cfg.quarantine_prefix}filename/business_date=unknown/{name}"
        sidecar_key = q_key + ".sidecar.json"
        sidecar = {"source_key": key, "correlation_id": cid, "reason": "filename",
                   "records": [{"file_rule": "filename"}]}
        # keep the raw bytes in the zone file
        _put(s3, bucket, q_key, raw_bytes, cid, source_sha256)
        _put(s3, bucket, sidecar_key, _sidecar_bytes(sidecar), cid, source_sha256)
        manifest = _write_manifest(s3, bucket, cfg, name, day, key, source_sha256, cid, ruleset,
                                   file_route="quarantine", rows_in=0,
                                   validated=0, flagged=0, duplicates=0,
                                   quarantined={"filename": 1}, balanced=False)
        logger.warning("file.quarantined", extra={"boundary": "s3->lambda", "key": key, "record_count": 0})
        return {"status": "quarantined", "manifest": manifest}

    # unreadable check (not UTF-8)
    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        q_key = f"{cfg.quarantine_prefix}unreadable/business_date={day}/{name}"
        sidecar_key = q_key + ".sidecar.json"
        sidecar = {"source_key": key, "correlation_id": cid, "reason": "unreadable",
                   "records": [{"file_rule": "unreadable"}]}
        _put(s3, bucket, q_key, raw_bytes, cid, source_sha256)
        _put(s3, bucket, sidecar_key, _sidecar_bytes(sidecar), cid, source_sha256)
        manifest = _write_manifest(s3, bucket, cfg, name, day, key, source_sha256, cid, ruleset,
                                   file_route="quarantine", rows_in=0,
                                   validated=0, flagged=0, duplicates=0,
                                   quarantined={"unreadable": 1}, balanced=False)
        logger.warning("file.quarantined", extra={"boundary": "s3->lambda", "key": key, "record_count": 0})
        return {"status": "quarantined", "manifest": manifest}

    # ---- step 6: parse CSV
    reader = csv.DictReader(io.StringIO(text))
    header = reader.fieldnames or []
    if not header:
        # empty file (no header)
        q_key = f"{cfg.quarantine_prefix}empty/business_date={day}/{name}"
        sidecar_key = q_key + ".sidecar.json"
        sidecar = {"source_key": key, "correlation_id": cid, "reason": "empty",
                   "records": [{"file_rule": "empty"}]}
        _put(s3, bucket, q_key, raw_bytes, cid, source_sha256)
        _put(s3, bucket, sidecar_key, _sidecar_bytes(sidecar), cid, source_sha256)
        manifest = _write_manifest(s3, bucket, cfg, name, day, key, source_sha256, cid, ruleset,
                                   file_route="quarantine", rows_in=0,
                                   validated=0, flagged=0, duplicates=0,
                                   quarantined={"empty": 1}, balanced=False)
        logger.warning("file.quarantined", extra={"boundary": "s3->lambda", "key": key, "record_count": 0})
        return {"status": "quarantined", "manifest": manifest}

    rows = list(reader)
    rows_in = len(rows)

    # ---- step 7: classify
    file_date_obj = business_date(name)
    file_result, results = classify(file_date_obj, header, rows, ruleset.file_rules, ruleset.rules, ruleset.ref)

    # ---- step 8: whole-file quarantine (schema)
    if file_result["route"] == "quarantine":
        reason = file_result.get("reason", "schema")
        q_key = f"{cfg.quarantine_prefix}{reason}/business_date={day}/{name}"
        sidecar_key = q_key + ".sidecar.json"

        # CSV zone file: header + source_line, all rows
        zone_buf = io.StringIO()
        w = csv.writer(zone_buf)
        raw_rows = _raw_csv_rows(text)
        w.writerow(header + ["source_line"])
        for idx, row_list in enumerate(raw_rows):
            w.writerow(row_list + [idx + 2])
        zone_body = zone_buf.getvalue().encode()

        sidecar = {"source_key": key, "correlation_id": cid, "reason": reason,
                   "records": [{"file_rule": file_result.get("rule", reason)}]}
        _put(s3, bucket, q_key, zone_body, cid, source_sha256)
        _put(s3, bucket, sidecar_key, _sidecar_bytes(sidecar), cid, source_sha256)
        manifest = _write_manifest(s3, bucket, cfg, name, day, key, source_sha256, cid, ruleset,
                                   file_route="quarantine", rows_in=rows_in,
                                   validated=0, flagged=0, duplicates=0,
                                   quarantined={reason: rows_in}, balanced=False)
        logger.warning("file.quarantined", extra={"boundary": "s3->lambda", "key": key, "record_count": rows_in})
        return {"status": "quarantined", "manifest": manifest}

    # ---- step 9: per-record routing
    raw_rows = _raw_csv_rows(text)

    by_zone = defaultdict(list)   # ("validated"|"quarantine", reason_or_"") -> list of result dicts
    counts = Counter()

    for r in results:
        route = r["route"]
        counts[route] += 1
        if route in ("validated", "flagged"):
            by_zone[("validated", "")].append(r)
        elif route == "quarantine":
            by_zone[("quarantine", r["reason"])].append(r)
        # duplicate / skipped: counted but not written to any zone

    # write zone files
    for (zone, reason), items in by_zone.items():
        if zone == "validated":
            zone_key = f"{cfg.validated_prefix}business_date={day}/{name}"
            zone_buf = io.StringIO()
            w = csv.writer(zone_buf)
            w.writerow(header + ["flags", "source_line"])
            for r in items:
                row_list = raw_rows[r["line"] - 2]
                w.writerow(row_list + [";".join(r["flags"]), r["line"]])
            zone_body = zone_buf.getvalue().encode()
            _put(s3, bucket, zone_key, zone_body, cid, source_sha256)
            logger.info("zone.written", extra={"boundary": "internal", "key": zone_key,
                                               "record_count": len(items)})
        else:
            zone_key = f"{cfg.quarantine_prefix}{reason}/business_date={day}/{name}"
            sidecar_key = zone_key + ".sidecar.json"
            zone_buf = io.StringIO()
            w = csv.writer(zone_buf)
            w.writerow(header + ["source_line"])
            for r in items:
                row_list = raw_rows[r["line"] - 2]
                w.writerow(row_list + [r["line"]])
            zone_body = zone_buf.getvalue().encode()
            sidecar = {"source_key": key, "correlation_id": cid, "reason": reason,
                       "records": [{"source_line": r["line"], "rule_id": r["rule"]} for r in items]}
            _put(s3, bucket, zone_key, zone_body, cid, source_sha256)
            _put(s3, bucket, sidecar_key, _sidecar_bytes(sidecar), cid, source_sha256)
            logger.info("zone.written", extra={"boundary": "internal", "key": zone_key,
                                               "record_count": len(items)})

    # ---- step 10: build balance and write manifest last
    quarantined_counts = dict(Counter(r["reason"] for r in results if r["route"] == "quarantine"))
    total_out = (counts["validated"] + counts["flagged"] + counts["duplicate"]
                 + sum(quarantined_counts.values()) + counts["skipped"])
    balanced = total_out == rows_in

    manifest = _write_manifest(s3, bucket, cfg, name, day, key, source_sha256, cid, ruleset,
                               file_route="accepted",
                               rows_in=rows_in,
                               validated=counts["validated"],
                               flagged=counts["flagged"],
                               duplicates=counts["duplicate"],
                               quarantined=quarantined_counts,
                               balanced=balanced)
    logger.info("file.processed", extra={"boundary": "s3->lambda", "key": key, "record_count": rows_in})
    return {"status": "processed", "manifest": manifest}


# ---- helpers

def _sidecar_bytes(sidecar):
    """Encode a sidecar dict to bytes using the platform line separator, matching Path.write_text() behaviour."""
    text = json.dumps(sidecar, indent=1)
    if os.linesep != "\n":
        text = text.replace("\n", os.linesep)
    return text.encode()


def _raw_csv_rows(text):
    """Return list of raw string lists (one per data row, header excluded)."""
    reader = csv.reader(io.StringIO(text))
    next(reader)   # skip header
    return list(reader)


def _write_manifest(s3, bucket, cfg, name, day, source_key, source_sha256, cid, ruleset,
                    file_route, rows_in, validated, flagged, duplicates, quarantined, balanced):
    manifest = {
        "source_key": source_key,
        "source_sha256": source_sha256,
        "business_date": day,
        "correlation_id": cid,
        "rules_sha256": ruleset.rules_sha256,
        "ref_data_sha256": ruleset.ref_data_sha256,
        "rows_in": rows_in,
        "file_route": file_route,
        "validated": validated,
        "flagged": flagged,
        "duplicates": duplicates,
        "quarantined": quarantined,
        "balanced": balanced,
    }
    manifest_key = f"{cfg.manifest_prefix}business_date={day}/{name}.manifest.json"
    _put(s3, bucket, manifest_key, json.dumps(manifest, indent=1).encode(), cid, source_sha256)
    return manifest
