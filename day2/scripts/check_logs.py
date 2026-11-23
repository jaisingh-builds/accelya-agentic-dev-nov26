#!/usr/bin/env python3
"""Check log lines against the course logging contract (docs/logging-contract.json).

Usage (from the repo root):
    python3 day2/scripts/check_logs.py day2/data/day1_sample.log
    aws logs tail $LG --since 1h --format short | python3 day2/scripts/check_logs.py -
    python3 day2/scripts/check_logs.py my.log --pii day2/data/cancellation_events.json

One JSON object per APPLICATION log record. Lambda platform records (START, END,
REPORT, INIT_START, ...) are skipped, and the timestamp prefix that
`aws logs tail --format short` puts in front of each record is stripped first.
Values are checked against per-key rules (type, pattern, enum, length), so a
payload can't hide inside an allowed key such as "message" or "key".
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


def _check_value(k, val, rule):
    """Typed, bounded values: a payload can't hide inside an allowed key."""
    t = rule.get("type")
    if t == "bool":
        return None if isinstance(val, bool) else f"'{k}' must be true/false"
    if t in ("int", "number"):
        ok = isinstance(val, int) if t == "int" else isinstance(val, (int, float))
        if isinstance(val, bool) or not ok:
            return f"'{k}' must be a {t}"
        if "min" in rule and val < rule["min"] or "max" in rule and val > rule["max"]:
            return f"'{k}' = {val} is out of range"
        return None
    if not isinstance(val, str):
        return f"'{k}' must be a string"
    if "enum" in rule and val not in rule["enum"]:
        return f"'{k}' = '{val[:30]}' is not one of {rule['enum']}"
    if "max" in rule and len(val) > rule["max"]:
        return f"'{k}' is {len(val)} chars (max {rule['max']})"
    if "pattern" in rule and not re.match(rule["pattern"], val):
        return f"'{k}' = '{val[:40]}{'…' if len(val) > 40 else ''}' doesn't match its rule (payload or free text?)"
    return None


def check_line(obj, raw, pii_values=()):
    """Return a list of violation strings for one parsed JSON log record."""
    c = CONTRACT
    v = []
    allowed = set(c["required"]) | set(c["optional"])
    for k in c["required"]:
        if k not in obj or obj[k] in ("", None):
            v.append(f"missing required '{k}'")
    for k, val in obj.items():
        if k not in allowed:
            v.append(f"key '{k}' is not in the contract")
            continue
        if isinstance(val, (dict, list)):
            v.append(f"'{k}' holds an object/array (payload-shaped)")
            continue
        if val is None:
            v.append(f"'{k}' is null (omit the key instead)")
            continue
        err = _check_value(k, val, c["value_rules"].get(k, {"max": 128}))
        if err:
            v.append(err)
    if obj.get("level") == "ERROR":
        for k in c["error_requires"]:
            if not isinstance(obj.get(k), str) or not obj.get(k).strip():
                v.append(f"ERROR record without a non-empty '{k}'")
    if len(raw.encode()) > c["max_line_bytes"]:
        v.append(f"record is {len(raw.encode())} bytes (max {c['max_line_bytes']})")
    for p in pii_values:
        if p in raw:
            v.append("PII value leaked into the log record")
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
            results.append((n, line, ["not one JSON object per record (free text?)"]))
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
