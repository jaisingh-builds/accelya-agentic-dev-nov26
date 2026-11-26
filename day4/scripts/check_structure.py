#!/usr/bin/env python3
"""Check the processor against the Day 4 coding standards (AGENTS.md + Module 4).

    python3 day4/scripts/check_structure.py

Folder structure, config-driven code, no hard-coding, thin handler, logger not print.
Exit code 0 = all checks pass.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "day4" / "src" / "processor"
TESTS = ROOT / "day4" / "tests"
REQUIRED = ["handler.py", "service.py", "validator.py", "config.py", "log.py",
            "rules/validation_rules.json", "rules/ref/airlines.json"]
HARDCODED = [
    (r"\b\d{12}\b", "hard-coded account id"),
    (r"arn:aws:", "hard-coded ARN"),
    (r"\b(us|eu|ap|sa|ca|me|af)-(east|west|north|south|central|northeast|southeast)-\d\b", "hard-coded region"),
    (r"accelya-m\d+-[a-z0-9]", "hard-coded resource name"),
    (r"(?<![\w.])print\(", "print() instead of the logger"),
]


def main():
    rows = []          # (ok, label, detail)

    for f in REQUIRED:
        hint = "cp day3/rules/validation_rules.json day4/src/processor/rules/" if f.endswith("rules.json") else "missing"
        rows.append(((SRC / f).exists(), f"tree: {f}", hint))
    rows.append((any(TESTS.glob("test_*.py")), "tree: tests/test_*.py", "missing"))

    for p in sorted(SRC.glob("*.py")):
        text = p.read_text(encoding="utf-8")
        code = re.sub(r'"""[\s\S]*?"""', "", text)
        code = "\n".join(l for l in code.splitlines() if not l.lstrip().startswith("#"))
        problems = [f"{what} ({m.group(0)!r})" for pat, what in HARDCODED for m in [re.search(pat, code)] if m]
        if p.name != "config.py" and ("os.environ" in code or "getenv" in code):
            problems.append("reads the environment (only config.py may)")
        if p.name == "handler.py":
            n = sum(1 for l in code.splitlines() if l.strip())
            if n > 70:
                problems.append(f"{n} code lines (max 70): move logic into service.py")
            if re.search(r"^\s*(from|import)\s+validator", code, re.M):
                problems.append("imports validator (call service.process_object instead)")
        if p.name == "validator.py" and ("boto3" in code or "logger" in code):
            problems.append("not pure (boto3 or logging in the rule engine)")
        rows.append((not problems, f"code: {p.name}", "; ".join(problems)))

    for ok, label, detail in rows:
        print(f"  {'PASS' if ok else 'FAIL'}  {label:<38} {'' if ok else detail}".rstrip())
    bad = sum(1 for ok, _, _ in rows if not ok)
    print(f"\nstructure: {len(rows) - bad}/{len(rows)} checks pass")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
