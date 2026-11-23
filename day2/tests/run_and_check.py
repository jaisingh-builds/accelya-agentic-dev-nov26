#!/usr/bin/env python3
"""Day 2 Lab 3 harness - runs YOUR cancellation handler on the fixture batch and checks:

  1. decisions match day2/data/expected_decisions.json (business rules + money rounding)
  2. malformed messages are reported as batchItemFailures (partial batch response)
  3. every log line conforms to docs/LOGGING_CONTRACT.md (via check_logs.py)
  4. no PII (passenger name, email, fare amount) appears in any log line
  5. every message's correlation_id appears in the logs (traceability)

Run from the repo root:   python3 day2/tests/run_and_check.py
Optional:                 python3 day2/tests/run_and_check.py path/to/handler.py
No AWS account needed.
"""
import importlib.util
import io
import json
import logging
import os
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "day2" / "scripts"))
import check_logs  # noqa: E402

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("POWERTOOLS_SERVICE_NAME", "accelya-m2-cancellation")
DATA = ROOT / "day2" / "data"
HANDLER = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "day2" / "src" / "cancellation" / "handler.py"


class Ctx:
    aws_request_id = "req-m2-0001"
    function_name = "accelya-m2-cancellation"
    memory_limit_in_mb = "256"

    def get_remaining_time_in_millis(self):
        return 30000


def load_handler(path):
    spec = importlib.util.spec_from_file_location("learner_handler", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.handler


def main():
    event = json.loads((DATA / "cancellation_events.json").read_text())
    expected = json.loads((DATA / "expected_decisions.json").read_text())
    pii = check_logs.load_pii(DATA / "cancellation_events.json")

    buf = io.StringIO()
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    lh = logging.StreamHandler(buf)
    lh.setFormatter(logging.Formatter("%(message)s"))   # like Lambda: your message is the line
    root.addHandler(lh)
    root.setLevel(logging.INFO)

    crash = None
    result = None
    try:
        handler = load_handler(HANDLER)
        with redirect_stdout(buf), redirect_stderr(buf):
            result = handler(event, Ctx())
    except NotImplementedError:
        print("The handler is still the stub. Build it with your assistant first (Lab 3, step 2).")
        sys.exit(1)
    except Exception as e:  # noqa: BLE001
        crash = f"{type(e).__name__}: {e}"

    lines = [l for l in buf.getvalue().splitlines() if l.strip()]
    checks = []

    if crash:
        checks.append(("handler runs on the whole batch without crashing", False, crash))
        result = {}
    else:
        checks.append(("handler runs on the whole batch without crashing", True, ""))

    got = {d.get("event_id"): d for d in (result or {}).get("decisions", [])}
    wrong = []
    for e in expected["decisions"]:
        g = got.get(e["event_id"])
        if not g:
            wrong.append(f"{e['event_id']}: missing")
            continue
        for k in ("decision", "refund_rule_id", "refund_amount"):
            if str(g.get(k)) != e[k]:
                wrong.append(f"{e['event_id']}: {k} = {g.get(k)!r}, expected {e[k]!r}")
    checks.append(("decisions match the refund rules (8 events)", not wrong, "; ".join(wrong[:4])))

    want_fail = {x["itemIdentifier"] for x in expected["batchItemFailures"]}
    got_fail = {x.get("itemIdentifier") for x in (result or {}).get("batchItemFailures", [])}
    checks.append(("malformed messages returned as batchItemFailures (m-07, m-08)", got_fail == want_fail,
                   f"got {sorted(got_fail)}" if got_fail != want_fail else ""))

    n_checked, bad = check_logs.check_lines(lines)
    detail = ""
    if bad:
        first = bad[0]
        detail = f"{len(bad)} bad line(s). First: {first[2][0]}  ->  {first[1][:80]}"
    checks.append((f"logs follow the logging contract ({n_checked} lines)", n_checked > 0 and not bad,
                   detail or ("no log lines at all" if n_checked == 0 else "")))

    leaks = [l for l in lines if any(p in l for p in pii)]
    checks.append(("no PII or fare amounts in the logs", not leaks, f"{len(leaks)} line(s) leak PII" if leaks else ""))

    cids = [r["messageAttributes"]["correlation_id"]["stringValue"]
            for r in event["Records"] if "correlation_id" in r.get("messageAttributes", {})]
    missing = [c for c in cids if not any(c in l for l in lines)]
    checks.append(("every message's correlation_id is traceable in the logs", not missing,
                   f"not found: {', '.join(missing[:4])}" if missing else ""))

    print(f"Day 2 harness  ·  handler: {HANDLER.relative_to(ROOT) if HANDLER.is_relative_to(ROOT) else HANDLER}\n")
    for name, ok, why in checks:
        print(("  PASS  " if ok else "  FAIL  ") + name + ("" if ok or not why else f"\n          {why}"))
    passed = sum(ok for _, ok, _ in checks)
    print(f"\n{passed}/{len(checks)} checks pass." + ("  Ready for peer review." if passed == len(checks) else
          "  Paste the FAIL lines into your assistant and iterate."))
    if bad and "-v" in sys.argv:
        for n, l, v in bad:
            print(f"\n  {l[:120]}\n    " + "\n    ".join(v))
    sys.exit(0 if passed == len(checks) else 1)


if __name__ == "__main__":
    main()
