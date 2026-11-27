"""Ticket-issue enrichment job (Module 5): validated CSV -> curated Parquet.

Runs as ONE container task (ECS Fargate run-task), not as a Lambda:
  - pyarrow is a heavy native dependency (the reason this job outgrew Lambda),
  - it rebuilds the whole curated dataset from validated/ on every run.

Input   s3://$BUCKET/validated/business_date=YYYY-MM-DD/ticket_issue_<AL>_YYYY-MM-DD.csv
Output  s3://$BUCKET/curated/ticket_issue/settlement_date=YYYY-MM-DD/airline_code=XX/part-00000.parquet
        s3://$BUCKET/manifests/curated/ticket_issue.manifest.json   (written last)

Configuration comes ONLY from environment variables (set in the task definition,
overridden per run with `aws ecs run-task --overrides`):
  BUCKET           required (unless --local-in/--local-out)
  LEARNER_ID       required, e.g. p07
  CORRELATION_ID   one per run; minted (m5-...) with a WARNING if missing
  INPUT_PREFIX     default validated/
  OUTPUT_PREFIX    default curated/ticket_issue/
  LOG_LEVEL        default INFO

Exit codes (read them in `aws ecs describe-tasks`):
  0 = curated dataset written and balanced
  2 = configuration error (missing env var)          -> fix the task definition, don't retry
  3 = no input files under INPUT_PREFIX               -> nothing to do, check the upstream job
  4 = rows in != rows out (manifest not written)      -> a bug: stop and look
  1 = anything else (AWS error, bad file)             -> logged with error_type only; the exit code is the signal
Local run (no AWS):  python3 enrich.py --local-in day5/data/validated --local-out /tmp/curated
"""
import argparse
import csv
import hashlib
import io
import json
import os
import sys
import time
import uuid
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

HERE = Path(__file__).resolve().parent
AIRLINES = {k: v for k, v in json.loads((HERE / "ref" / "airlines.json").read_text()).items() if not k.startswith("_")}
CONTRACT_VERSION = "1.1"

SCHEMA = pa.schema([
    ("pnr", pa.string()),
    ("ticket_number", pa.string()),
    ("airline_name", pa.string()),
    ("fare_class", pa.string()),
    ("origin", pa.string()),
    ("destination", pa.string()),
    ("issue_date", pa.date32()),
    ("fare_amount", pa.decimal128(12, 2)),
    ("currency", pa.string()),
    ("agency_iata", pa.string()),
    ("flags", pa.string()),
    ("business_date", pa.date32()),
    ("source_key", pa.string()),
    ("source_line", pa.int32()),
])
# settlement_date and airline_code are NOT columns in the file: they are the partition folders.


class ConfigError(Exception):
    """A required environment variable is missing. Exit 2: retrying won't help."""


# ---------- logging: the same contract as Day 2 and Day 4, from a container ----------
class Log:
    LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40}

    def __init__(self, service, learner_id, correlation_id, level="INFO"):
        self.base = {"service": service, "correlation_id": correlation_id, "boundary": "internal",
                     "learner_id": learner_id, "contract_version": CONTRACT_VERSION}
        self.min = self.LEVELS.get(level.upper(), 20)

    def _emit(self, level, message, **keys):
        if self.LEVELS[level] < self.min:
            return
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        rec = {"ts": ts, "level": level, "service": self.base["service"],
               "correlation_id": self.base["correlation_id"], "boundary": keys.pop("boundary", "internal"),
               "message": message}
        rec.update({k: v for k, v in self.base.items() if k not in rec})
        rec.update(keys)
        print(json.dumps(rec, separators=(",", ":")), flush=True)   # stdout -> awslogs -> CloudWatch

    def info(self, m, **k): self._emit("INFO", m, **k)
    def warning(self, m, **k): self._emit("WARNING", m, **k)
    def error(self, m, **k): self._emit("ERROR", m, **k)


# ---------- pure functions (unit-tested, no AWS) ----------
def settlement_date(issue: date) -> date:
    """Simplified BSP weekly cycle: reporting week Monday-Sunday, agencies remit on the following Friday."""
    week_end = issue + timedelta(days=6 - issue.weekday())      # the Sunday that closes the week
    return week_end + timedelta(days=5)                         # the Friday after


def enrich_row(row: dict, source_key: str) -> dict:
    origin, destination = row["od_pair"].split("-")
    issue = date.fromisoformat(row["issue_date"])
    return {
        "pnr": row["pnr"],
        "ticket_number": row["ticket_number"],
        "airline_code": row["airline_code"],
        "airline_name": AIRLINES.get(row["airline_code"], {}).get("name", "unknown"),
        "fare_class": row["fare_class"],
        "origin": origin,
        "destination": destination,
        "issue_date": issue,
        "fare_amount": Decimal(row["fare_amount"]),             # Decimal, never float: this is money
        "currency": row["currency"],
        "agency_iata": row["agency_iata"],
        "flags": row.get("flags", ""),
        "business_date": date.fromisoformat(source_key.split("business_date=")[1][:10]),
        "source_key": source_key,
        "source_line": int(row["source_line"]),
        "settlement_date": settlement_date(issue),
    }


def partition_key(prefix: str, settle: date, airline: str) -> str:
    return f"{prefix}settlement_date={settle.isoformat()}/airline_code={airline}/part-00000.parquet"


def to_parquet(rows: list) -> bytes:
    """One partition's rows -> Parquet bytes. Sorted, so the same input gives the same bytes."""
    rows = sorted(rows, key=lambda r: (r["business_date"], r["ticket_number"], r["source_line"]))
    table = pa.Table.from_pylist([{f.name: r[f.name] for f in SCHEMA} for r in rows], schema=SCHEMA)
    buf = io.BytesIO()
    pq.write_table(table, buf, compression="snappy")
    return buf.getvalue()


def build(files: dict, out_prefix: str):
    """files: {source_key: csv_text}. Returns ({output_key: parquet_bytes}, manifest)."""
    parts = defaultdict(list)
    rows_in = 0
    for key in sorted(files):
        for row in csv.DictReader(io.StringIO(files[key])):
            rows_in += 1
            r = enrich_row(row, key)
            parts[(r["settlement_date"], r["airline_code"])].append(r)
    outputs = {partition_key(out_prefix, s, a): to_parquet(rs) for (s, a), rs in sorted(parts.items())}
    rows_out = sum(len(rs) for rs in parts.values())
    manifest = {
        "dataset": out_prefix.rstrip("/"),
        "inputs": len(files),
        "input_sha256": hashlib.sha256("".join(f"{k}:{hashlib.sha256(v.encode()).hexdigest()}" for k, v in sorted(files.items())).encode()).hexdigest(),
        "rows_in": rows_in,
        "rows_out": rows_out,
        "partitions": {k[len(out_prefix):].rsplit("/", 1)[0]: len(parts[(s, a)])
                       for (s, a) in sorted(parts) for k in [partition_key(out_prefix, s, a)]},
        "by_airline": dict(sorted(Counter(r["airline_code"] for rs in parts.values() for r in rs).items())),
        "balanced": rows_in == rows_out,
        "job_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:16],
    }
    return outputs, manifest


# ---------- I/O ----------
def read_s3(s3, bucket, prefix, log):
    files = {}
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            if obj["Key"].endswith(".csv"):
                files[obj["Key"]] = s3.get_object(Bucket=bucket, Key=obj["Key"])["Body"].read().decode("utf-8")
                log.info("file.read", bucket=bucket, key=obj["Key"], bytes=obj["Size"])
    return files


def read_local(root: Path, prefix: str):
    return {prefix + str(p.relative_to(root)).replace(os.sep, "/"): p.read_text(encoding="utf-8")
            for p in sorted(root.rglob("*.csv"))}


def env(name, default=None):
    v = os.environ.get(name, default)
    if v in (None, ""):
        raise ConfigError(name)
    return v


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--local-in", help="read validated CSVs from this folder instead of S3")
    ap.add_argument("--local-out", help="write Parquet + manifest to this folder instead of S3")
    a = ap.parse_args(argv)
    t0 = time.monotonic()

    learner = os.environ.get("LEARNER_ID", "")
    cid = os.environ.get("CORRELATION_ID", "")
    minted = not cid
    if minted:
        cid = "m5-" + uuid.uuid4().hex[:12]
    log = Log(f"accelya-m5-{learner or 'unknown'}-enrich", learner or "unknown", cid, os.environ.get("LOG_LEVEL", "INFO"))
    if minted:
        log.warning("correlation_id.missing")
    try:
        if not learner:
            raise ConfigError("LEARNER_ID")
        in_prefix = env("INPUT_PREFIX", "validated/")
        out_prefix = env("OUTPUT_PREFIX", "curated/ticket_issue/")
        local = bool(a.local_in or a.local_out)
        bucket = None if local else env("BUCKET")
        log.info("job.started", boundary="s3(validated)->ecs(enrich)", bucket=bucket or "local")

        if local:
            files = read_local(Path(a.local_in), in_prefix)
        else:
            import boto3
            s3 = boto3.client("s3")
            files = read_s3(s3, bucket, in_prefix, log)
        if not files:
            log.warning("input.empty", key=in_prefix)
            return 3

        outputs, manifest = build(files, out_prefix)
        manifest["correlation_id"] = cid
        if not manifest["balanced"]:
            log.error("job.unbalanced", error_type="Unbalanced", record_count=manifest["rows_in"])
            return 4
        for key, body in outputs.items():
            if local:
                p = Path(a.local_out) / key
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(body)
            else:
                s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType="application/vnd.apache.parquet")
            log.info("partition.written", boundary="ecs(enrich)->s3(curated)", key=key, bytes=len(body))
        mkey = "manifests/" + out_prefix.rstrip("/") + ".manifest.json"
        mbody = json.dumps(manifest, indent=1).encode()
        if local:
            p = Path(a.local_out) / mkey
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(mbody)
        else:
            s3.put_object(Bucket=bucket, Key=mkey, Body=mbody, ContentType="application/json")
        log.info("job.finished", key=mkey, record_count=manifest["rows_out"],
                 duration_ms=round((time.monotonic() - t0) * 1000, 1))
        return 0
    except ConfigError as e:
        log.error("config.missing", error_type="ConfigError", error_code=str(e))
        return 2
    except Exception as e:   # unknown: log the type (and AWS error code) only - no message, no traceback
        code = getattr(e, "response", {}).get("Error", {}).get("Code") if hasattr(e, "response") else None
        log.error("job.failed", error_type=type(e).__name__, **({"error_code": code} if code else {}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
