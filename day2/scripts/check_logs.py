#!/usr/bin/env python3
"""Check log lines against the course logging contract (docs/logging-contract.json).

Usage (from the repo root):
    python3 day2/scripts/check_logs.py day2/data/day1_sample.log
    aws logs tail $LG --since 1h --format short | python3 day2/scripts/check_logs.py -
    python3 day2/scripts/check_logs.py my.log --pii day2/data/cancellation_events.json

Lambda platform lines (START, END, REPORT, INIT_START) and the timestamp prefix
added by `aws logs tail --format short` are skipped automatically.
Exit code 0 = conformant, 1 = violations found.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = json.loads((ROOT / "docs" / "logging-contract.json").read_text())
PLATFORM = re.compile(r"^(START|END|REPORT|INIT_START|INIT_REPORT|EXTENSION|TELEMETRY|PLATFORM)\b")
TAIL_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\S*\s+")
PII_FIELDS = ("passenger_name", "contact_email", "contact_phone", "fare_amount")


def load_pii(path):
    """Collect PII values from an SQS-shaped fixture file so we can detect leaks by value."""
    values = set()
    data = json.loads(Path(path).read_text())
    for rec in data.get("Records", []):
        try:
            body = json.loads(rec.get("body", "{}"))
        except json.JSONDecodeError:
            continue
        for f in PII_FIELDS:
            v = body.get(f)
            if isinstance(v, str) and len(v) >= 4:
                values.add(v)
    return values


def check_line(obj, raw, pii_values=()):
    """Return a list of violation strings for one parsed JSON log line."""
    c = CONTRACT
    v = []
    allowed = set(c["required"]) | set(c["optional"])
    for k in c["required"]:
        if k not in obj or obj[k] in ("", None):
            v.append(f"missing required '{k}'")
    for k in obj:
        if k not in allowed:
            v.append(f"key '{k}' is not in the contract")
    for k, val in obj.items():
        if isinstance(val, (dict, list)):
            v.append(f"'{k}' holds an object/array (payload-shaped)")
    lvl = obj.get("level")
    if lvl is not None and lvl not in c["levels"]:
        v.append(f"level '{lvl}' not one of {c['levels']}")
    if lvl == "ERROR":
        for k in c["error_requires"]:
            if k not in obj:
                v.append(f"ERROR line without '{k}'")
    if "message" in obj and isinstance(obj["message"], str) and not re.match(c["message_pattern"], obj["message"]):
        v.append(f"message '{obj['message'][:40]}' is not a dotted event name")
    if "boundary" in obj and isinstance(obj["boundary"], str) and not re.match(c["boundary_pattern"], obj["boundary"]):
        v.append(f"boundary '{obj['boundary']}' is not source->target or internal")
    if "ts" in obj and isinstance(obj["ts"], str) and not re.match(c["ts_pattern"], obj["ts"]):
        v.append(f"ts '{obj['ts']}' is not ISO-8601 UTC ending in Z")
    if len(raw.encode()) > c["max_line_bytes"]:
        v.append(f"line is {len(raw.encode())} bytes (max {c['max_line_bytes']})")
    for p in pii_values:
        if p in raw:
            v.append("PII value leaked into the log line")
            break
    return v


def check_lines(lines, pii_values=()):
    """Check an iterable of raw lines. Returns (checked_count, [(line_no, text, [violations])])."""
    results, checked = [], 0
    for n, raw in enumerate(lines, 1):
        line = TAIL_PREFIX.sub("", raw.rstrip("\n"))
        if not line.strip() or PLATFORM.match(line):
            continue
        checked += 1
        try:
            obj = json.loads(line)
            if not isinstance(obj, dict):
                raise ValueError
        except ValueError:
            results.append((n, line, ["not a single JSON object"]))
            continue
        viol = check_line(obj, line, pii_values)
        if viol:
            results.append((n, line, viol))
    return checked, results


def main():
    args = sys.argv[1:]
    if not args:
        sys.exit(__doc__)
    pii = set()
    if "--pii" in args:
        i = args.index("--pii")
        pii = load_pii(args[i + 1])
        del args[i:i + 2]
    src = sys.stdin if args[0] == "-" else open(args[0], encoding="utf-8")
    checked, bad = check_lines(src, pii)
    print(f"Logging contract v{CONTRACT['version']}  ·  {checked} application lines checked")
    for n, line, viol in bad:
        print(f"\n  line {n}: {line[:110]}{'…' if len(line) > 110 else ''}")
        for x in viol:
            print(f"    ✗ {x}")
    print(f"\n{'CONFORMANT' if not bad else f'{len(bad)} non-conformant line(s)'}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
