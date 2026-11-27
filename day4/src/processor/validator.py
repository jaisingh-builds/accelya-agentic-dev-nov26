"""Rule engine: Day 3's classifier as a library. Pure functions - no S3, no logging, no globals.

Given the header and rows of one batch file, it returns where every record goes and why.
Rule types and phases are exactly those of day3/scripts/classify.py (see day3/rules/RULE_TYPES.md):
    file rules (header) -> required -> format -> referential -> business -> late flag -> duplicates
"""
import hashlib
import json
import re
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

PHASES = ["required", "format", "referential", "business", "late", "duplicate"]
ROUTES = {"quarantine", "flag", "skip"}
FILE_DATE = re.compile(r"_(\d{4}-\d{2}-\d{2})")


class RuleError(Exception):
    """The rules file itself is wrong. A deployment problem, not a data problem."""


class FileNameError(Exception):
    """The object name carries no YYYY-MM-DD business date."""


def business_date(name):
    m = FILE_DATE.search(Path(name).name)
    if not m:
        raise FileNameError("no YYYY-MM-DD business date in the file name")
    try:
        return date.fromisoformat(m.group(1))
    except ValueError as e:
        raise FileNameError("invalid business date in the file name") from e


def load_rules(path):
    """Return (file_rules, record_rules sorted by phase, rules_sha256[:16])."""
    raw = Path(path).read_bytes()
    try:
        data = json.loads(raw)
    except ValueError as e:
        raise RuleError(f"rules file is not valid JSON: {e.__class__.__name__}") from e
    rules = [r for r in data.get("rules", []) if isinstance(r, dict)]
    file_rules = [r for r in data.get("file_rules", []) if isinstance(r, dict)]
    seen, problems = set(), []
    for r in rules + file_rules:
        rid = r.get("id")
        if not rid or rid in seen:
            problems.append(f"missing or duplicate id {rid!r}")
        seen.add(rid)
    for r in rules:
        if r.get("reason") not in PHASES:
            problems.append(f"{r.get('id')}: bad reason")
        if r.get("on_fail") not in ROUTES:
            problems.append(f"{r.get('id')}: bad on_fail")
    if problems:
        raise RuleError("; ".join(problems))
    rules.sort(key=lambda r: PHASES.index(r["reason"]))
    return file_rules, rules, hashlib.sha256(raw).hexdigest()[:16]


def load_ref(ref_dir):
    """Return (reference data dict, ref_data_sha256[:16])."""
    ref, blob = {}, b""
    for p in sorted(Path(ref_dir).glob("*.json")):
        raw = p.read_bytes()
        blob += raw
        data = json.loads(raw)
        ref[p.stem] = set(data["codes"]) if "codes" in data else {k: v for k, v in data.items() if not k.startswith("_")}
    return ref, hashlib.sha256(blob).hexdigest()[:16]


def _val(rule, row):
    return (row.get(rule["field"]) or "").strip()


def passes(rule, row, ctx):
    """True if the record PASSES the rule."""
    t = rule["type"]
    if t == "required":
        return all((row.get(f) or "").strip() for f in rule["fields"])
    v = _val(rule, row)
    if t == "regex":
        return re.fullmatch(rule["pattern"], v) is not None
    if t == "in_ref":
        return v in ctx["ref"][rule["ref"]]
    if t == "od_airports_in_ref":
        parts = v.split("-")
        return len(parts) == 2 and all(p in ctx["ref"]["airports"] for p in parts)
    if t == "od_distinct":
        parts = v.split("-")
        return len(parts) == 2 and parts[0] != parts[1]
    if t == "ticket_prefix":
        airline = ctx["ref"]["airlines"].get((row.get(rule["airline_field"]) or "").strip())
        return bool(airline) and v.split("-")[0] == airline["ticket_prefix"]
    if t == "decimal_places":
        try:
            d = Decimal(v)
        except InvalidOperation:
            return False
        return d.is_finite() and -d.as_tuple().exponent <= rule["max_places"]
    if t == "min_exclusive":
        try:
            return Decimal(v) > Decimal(str(rule["value"]))
        except InvalidOperation:
            return False
    if t == "date_format":
        try:
            datetime.strptime(v, rule.get("format", "%Y-%m-%d"))
            return True
        except ValueError:
            return False
    if t == "date_not_after_file_date":
        try:
            return date.fromisoformat(v) <= ctx["file_date"]
        except ValueError:
            return False
    if t == "date_within_days":
        try:
            return date.fromisoformat(v) >= ctx["file_date"] - timedelta(days=rule["days"])
        except ValueError:
            return False
    if t == "unique":
        return True
    raise RuleError(f"rule {rule.get('id')}: unknown type {t!r}")


def classify(file_date, header, rows, file_rules, rules, ref):
    """Route every row. Returns (file_result, results).

    file_result: {"route": "accepted"} or {"route": "quarantine", "reason", "rule"}
    results: one dict per row: line (CSV line number, header = 1), route
             (validated | flagged | quarantine | duplicate | skipped), reason, rule, flags
    """
    for fr in file_rules:
        if fr["type"] == "header" and list(header) != fr["columns"]:
            return {"route": "quarantine", "reason": fr.get("reason", "schema"), "rule": fr["id"]}, []
    ctx = {"ref": ref, "file_date": file_date}
    results, seen = [], {}
    for i, row in enumerate(rows):
        out = {"line": i + 2, "route": "validated", "reason": "", "rule": "", "flags": []}
        for r in rules:
            if r["type"] == "unique" or passes(r, row, ctx):
                continue
            if r["on_fail"] == "flag":
                out["flags"].append(r["id"])
                if out["route"] == "validated":
                    out.update(route="flagged", reason=r["reason"], rule=r["id"])
                continue
            out.update(route="quarantine" if r["on_fail"] == "quarantine" else "skipped", reason=r["reason"], rule=r["id"])
            break
        if out["route"] in ("validated", "flagged"):
            for r in (x for x in rules if x["type"] == "unique"):
                k = (row.get(r["field"]) or "").strip()
                if k in seen:
                    route = {"skip": "duplicate", "quarantine": "quarantine", "flag": "flagged"}[r["on_fail"]]
                    out.update(route=route, reason=r["reason"], rule=r["id"])
                    break
                seen[k] = out["line"]
        results.append(out)
    return {"route": "accepted"}, results
