"""Fixture test for the ticket-refund Lambda. No AWS account needed.

Run from the repo root:   python3 day2/src/ticket-refund/test_handler.py

Covers every refund window, a malformed body, a missing-field body and the
logging contract (day2/scripts/check_logs.py).
"""
import io
import json
import os
import sys
from pathlib import Path

os.environ.update(AWS_DEFAULT_REGION="us-east-1", LEARNER_ID="test")
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "day2" / "scripts"))

import handler  # noqa: E402
from check_logs import check_lines  # noqa: E402

CANC = "2026-12-01T12:00:00Z"


def rec(mid, body, cid=None):
    attrs = {"correlation_id": {"stringValue": cid, "dataType": "String"}} if cid else {}
    return {"messageId": mid, "body": json.dumps(body) if isinstance(body, dict) else body,
            "messageAttributes": attrs}


def ticket(**kw):
    base = {"pnr": "QX7P2L", "ticket_number": "098-2304511901", "airline_code": "AI",
            "fare_class": "K", "fare_amount": "1000.00",
            "departure_datetime": "2026-12-10T12:00:00Z", "cancellation_timestamp": CANC}
    base.update(kw)
    return base


EVENT = {"Records": [
    rec("m-01", ticket(departure_datetime="2026-12-10T12:00:00Z"), "c-w1"),   # 9 d   -> R-WEEK 900.00
    rec("m-02", ticket(departure_datetime="2026-12-08T12:00:01Z"), "c-w2"),   # >=7 d -> R-WEEK
    rec("m-03", ticket(departure_datetime="2026-12-04T12:00:00Z"), "c-w3"),   # 3 d   -> R-TWODAYS 500.00
    rec("m-04", ticket(departure_datetime="2026-12-03T12:00:00Z"), "c-w4"),   # exactly 2 d -> R-TWODAYS
    rec("m-05", ticket(departure_datetime="2026-12-02T00:00:00Z"), "c-w5"),   # 12 h  -> R-REVIEW
    rec("m-06", ticket(departure_datetime="2026-12-01T16:30:00Z"), "c-w6"),   # 4.5 h -> R-LASTMIN
    rec("m-07", ticket(departure_datetime="2026-12-01T18:00:00Z"), "c-w7"),   # exactly 6 h -> R-REVIEW
    rec("m-08", ticket(departure_datetime="2026-12-01T11:00:00Z"), "c-w8"),   # past  -> R-DEPARTED
    rec("m-09", '{"pnr": "QX7P2L", "ticket_number": '),                        # bad JSON
    rec("m-10", {k: v for k, v in ticket().items() if k != "fare_amount"}, "c-w10"),  # missing field
    rec("m-11", ticket(departure_datetime="2026-12-01T12:00:00"), "c-w11"),   # naive dt, no tz
    rec("m-12", ticket(departure_datetime="2026-12-20T00:00:00Z")),           # no cid -> mint one
]}


class Ctx:
    aws_request_id = "test-req-0001"
    memory_limit_in_mb = "256"


def run():
    buf, real = io.StringIO(), sys.stdout
    sys.stdout = buf
    try:
        result = handler.handler(EVENT, Ctx())
    finally:
        sys.stdout = real
    return result, buf.getvalue().splitlines()


def main():
    result, raw = run()
    lines = [json.loads(l) for l in raw if l.strip()]
    got = {d["ticket_number"]: d for d in result["decisions"]}
    fails = sorted(x["itemIdentifier"] for x in result["batchItemFailures"])

    def dec(i):
        d = result["decisions"][i]
        return d["refund_rule_id"], d["decision"], d["refund_amount"]

    checks = [
        ("m-01 9 days -> R-WEEK APPROVE 900.00", dec(0) == ("R-WEEK", "APPROVE", "900.00")),
        ("m-02 just over 7 d -> R-WEEK", dec(1)[0] == "R-WEEK"),
        ("m-03 3 days -> R-TWODAYS APPROVE 500.00", dec(2) == ("R-TWODAYS", "APPROVE", "500.00")),
        ("m-04 exactly 2 days -> R-TWODAYS (boundary)", dec(3)[0] == "R-TWODAYS"),
        ("m-05 12 h -> R-REVIEW MANUAL_REVIEW", dec(4) == ("R-REVIEW", "MANUAL_REVIEW", "0.00")),
        ("m-06 4.5 h -> R-LASTMIN REJECT 0.00", dec(5) == ("R-LASTMIN", "REJECT", "0.00")),
        ("m-07 exactly 6 h -> R-REVIEW (boundary)", dec(6)[0] == "R-REVIEW"),
        ("m-08 departed -> R-DEPARTED REJECT", dec(7) == ("R-DEPARTED", "REJECT", "0.00")),
        ("m-09..m-11 in batchItemFailures", fails == ["m-09", "m-10", "m-11"]),
        ("each failure logged ERROR with error_type",
         all(any(l["message_id"] == m and l["level"] == "ERROR" and l.get("error_type") for l in lines)
             for m in ("m-09", "m-10", "m-11"))),
        ("m-12 minted one correlation_id on all its lines",
         len({l["correlation_id"] for l in lines if l.get("message_id") == "m-12"}) == 1),
        ("every line conforms to docs/logging-contract.json", not check_lines(raw)[1]),
    ]
    for name, ok in checks:
        print(("  PASS  " if ok else "  FAIL  ") + name)
    failed = [n for n, ok in checks if not ok]
    print("\nALL PASS" if not failed else f"\n{len(failed)} failing")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
