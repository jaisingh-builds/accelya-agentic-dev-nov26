#!/usr/bin/env python3
"""Day 2 Lab 3 harness - runs a cancellation handler on the fixture batch and checks:

  1. it runs on the whole batch without crashing
  2. decisions match day2/data/expected_decisions.json (business rules + money rounding)
  3. m-07 and m-08 come back in batchItemFailures AND each has an ERROR log record with a
     non-empty error_type
  4. every log record conforms to docs/LOGGING_CONTRACT.md (via check_logs.py)
  5. no PII (passenger name, email, fare amount) appears in any log record
  6. every SQS record is traceable: its log records carry its correlation_id, and m-10 (no
     correlation attribute) carries one valid, newly minted id

Isolation: the handler runs in a SEPARATE Python process with an empty home folder, a throwaway
working directory, no AWS credentials or config, and outbound network calls blocked. That is why
you can safely run a peer's handler here. It is a classroom guard, not a security sandbox.

Run from the repo root:
    python3 day2/tests/run_and_check.py                    # your handler
    python3 day2/tests/run_and_check.py /tmp/peer.py       # someone else's
    python3 day2/tests/run_and_check.py -v                 # also list every bad log record
No AWS account needed.
"""
import argparse
import re
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "day2" / "scripts"))
import check_logs  # noqa: E402

DATA = ROOT / "day2" / "data"
RUNNER = Path(__file__).resolve().parent / "_isolated_runner.py"
DEFAULT_HANDLER = ROOT / "day2" / "src" / "cancellation" / "handler.py"
CID_RULE = check_logs.CONTRACT["value_rules"]["correlation_id"]["pattern"]


def run_isolated(handler, event_file):
    with tempfile.TemporaryDirectory(prefix="m2-harness-") as tmp:
        home = Path(tmp, "home")
        home.mkdir()
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": str(home), "USERPROFILE": str(home),
            "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8",
            "AWS_CONFIG_FILE": os.devnull, "AWS_SHARED_CREDENTIALS_FILE": os.devnull,
            "AWS_EC2_METADATA_DISABLED": "true", "AWS_DEFAULT_REGION": "us-east-1",
            "POWERTOOLS_SERVICE_NAME": "accelya-m2-cancellation",
        }
        for k in ("SYSTEMROOT", "LANG", "TZ"):          # SYSTEMROOT: Python needs it on Windows
            if k in os.environ:
                env[k] = os.environ[k]
        out = Path(tmp, "out.json")
        try:
            p = subprocess.run([sys.executable, str(RUNNER), str(handler), str(event_file), str(out)],
                               cwd=tmp, env=env, capture_output=True, text=True, timeout=60)
        except subprocess.TimeoutExpired:
            return {"stub": False, "crash": "timed out after 60 s", "result": None, "lines": []}
        if not out.exists():
            return {"stub": False, "crash": f"runner failed: {(p.stderr or p.stdout).strip()[-300:]}",
                    "result": None, "lines": []}
        return json.loads(out.read_text(encoding="utf-8"))


def parse(lines):
    recs = []
    for l in lines:
        try:
            o = json.loads(l)
        except ValueError:
            continue
        if isinstance(o, dict):
            recs.append(o)
    return recs


def main():
    ap = argparse.ArgumentParser(description="Day 2 Lab 3 harness (6 checks, no AWS needed)")
    ap.add_argument("handler", nargs="?", default=str(DEFAULT_HANDLER), help="handler file (default: your handler.py)")
    ap.add_argument("-v", "--verbose", action="store_true", help="list every non-conformant log record")
    args = ap.parse_args()
    handler = Path(args.handler).resolve()
    if not handler.is_file():
        sys.exit(f"No such handler file: {args.handler}")

    event_file = DATA / "cancellation_events.json"
    event = json.loads(event_file.read_text())
    expected = json.loads((DATA / "expected_decisions.json").read_text())
    pii = check_logs.load_pii(event_file)

    run = run_isolated(handler, event_file)
    if run["stub"]:
        print("The handler is still the stub. Build it with your assistant first (Lab 3, step 2).")
        sys.exit(1)
    lines = run["lines"]
    recs = parse(lines)
    result = run["result"] if isinstance(run["result"], dict) else {}
    checks = []

    # 1 - runs
    checks.append(("handler runs on the whole batch without crashing", not run["crash"], run["crash"] or ""))

    # 2 - decisions
    got = {d.get("event_id"): d for d in result.get("decisions", []) if isinstance(d, dict)}
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

    # record lookups: messageId -> correlation id given in the fixture (None for m-10), event_id from the body
    given = {}
    body_event = {}
    for r in event["Records"]:
        given[r["messageId"]] = r.get("messageAttributes", {}).get("correlation_id", {}).get("stringValue")
        try:
            body_event[r["messageId"]] = json.loads(r["body"]).get("event_id")
        except ValueError:
            body_event[r["messageId"]] = None

    def records_for(mid):
        cid, eid = given[mid], body_event[mid]
        return [o for o in recs if (cid and o.get("correlation_id") == cid) or o.get("message_id") == mid
                or (eid and o.get("event_id") == eid)]

    # 3 - partial batch response + ERROR logs for the failed records
    want_fail = {x["itemIdentifier"] for x in expected["batchItemFailures"]}
    items = result.get("batchItemFailures", [])
    got_fail = {x.get("itemIdentifier") for x in items if isinstance(x, dict)}
    why = [] if got_fail == want_fail else [f"batchItemFailures = {sorted(got_fail)}, expected {sorted(want_fail)}"]
    for mid in sorted(want_fail):
        errs = [o for o in records_for(mid) if o.get("level") == "ERROR"
                and isinstance(o.get("error_type"), str) and o["error_type"].strip()]
        if not errs:
            why.append(f"{mid}: no ERROR log record with a non-empty error_type")
    checks.append(("m-07, m-08 in batchItemFailures, each logged at ERROR with error_type", not why, "; ".join(why)))

    # 4 - contract
    n_checked, bad = check_logs.check_lines(lines)
    detail = ""
    if bad:
        first = bad[0]
        detail = f"{len(bad)} bad record(s). First: {first[2][0]}  ->  {first[1][:80]}"
    checks.append((f"logs follow the logging contract ({n_checked} records)", n_checked > 0 and not bad,
                   detail or ("no log records at all" if n_checked == 0 else "")))

    # 5 - PII
    leaks = [l for l in lines if any(p in l for p in pii)]
    checks.append(("no PII or fare amounts in the logs", not leaks, f"{len(leaks)} record(s) leak PII" if leaks else ""))

    # 6 - traceability, per record
    why = []
    for mid, cid in given.items():
        mine = records_for(mid)
        if cid:
            if not any(o.get("correlation_id") == cid for o in mine):
                why.append(f"{mid}: no log record with correlation_id {cid}")
            continue
        # no attribute (m-10): a minted id, valid, new, and the same on all of its records
        ids = {o.get("correlation_id") for o in mine}
        if not mine:
            why.append(f"{mid}: no log record (log message_id or event_id so it can be traced)")
        elif len(ids) != 1:
            why.append(f"{mid}: {len(ids)} different correlation_ids on its records")
        else:
            m = ids.pop()
            if not isinstance(m, str) or not re.match(CID_RULE, m) or m in given.values():
                why.append(f"{mid}: minted correlation_id {m!r} is missing, invalid or reused")
    checks.append(("every record traceable by correlation_id (minted for m-10)", not why, "; ".join(why[:3])))

    shown = handler.relative_to(ROOT) if handler.is_relative_to(ROOT) else handler
    print(f"Day 2 harness  ·  handler: {shown}  ·  isolated: separate process, empty HOME, no AWS creds, network blocked\n")
    for name, ok, why in checks:
        print(("  PASS  " if ok else "  FAIL  ") + name + ("" if ok or not why else f"\n          {why}"))
    passed = sum(ok for _, ok, _ in checks)
    print(f"\n{passed}/{len(checks)} checks pass." + ("  Ready for peer review." if passed == len(checks) else
          "  Paste the FAIL lines into your assistant and iterate."))
    if bad and args.verbose:
        for _n, l, v in bad:
            print(f"\n  {l[:120]}\n    " + "\n    ".join(v))
    elif bad:
        print("  (add -v to list every non-conformant log record)")
    sys.exit(0 if passed == len(checks) else 1)


if __name__ == "__main__":
    main()
