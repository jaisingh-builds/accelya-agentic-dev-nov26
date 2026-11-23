"""Child process used by run_and_check.py. Do not run directly.

Runs ONE handler file on the fixture batch inside a separate Python process that the parent
starts with an empty home folder, no AWS credentials or config, and a throwaway working dir.
Before the handler is imported, Python's standard socket calls (DNS lookup, connect) are blocked in
this process. These are Python-level guards for the classroom, not an OS-level sandbox.

Usage (by the parent only): python3 _isolated_runner.py <handler.py> <event.json> <out.json>
"""
import importlib.util
import io
import json
import logging
import socket
import sys
from contextlib import redirect_stderr, redirect_stdout


class NetworkBlocked(OSError):
    pass


def _blocked(*_a, **_k):
    raise NetworkBlocked("network access is disabled in the Lab 3 harness")


# Block DNS and outbound connections before any learner/peer code is imported.
socket.getaddrinfo = _blocked
socket.create_connection = _blocked
socket.socket.connect = _blocked
socket.socket.connect_ex = _blocked


class Ctx:
    aws_request_id = "req-m2-0001"
    function_name = "accelya-m2-cancellation"
    memory_limit_in_mb = "256"

    def get_remaining_time_in_millis(self):
        return 30000


def main():
    handler_path, event_path, out_path = sys.argv[1:4]
    event = json.loads(open(event_path, encoding="utf-8").read())

    buf = io.StringIO()
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    lh = logging.StreamHandler(buf)
    lh.setFormatter(logging.Formatter("%(message)s"))   # like Lambda: your message is the line
    root.addHandler(lh)
    root.setLevel(logging.INFO)

    out = {"stub": False, "crash": None, "result": None}
    try:
        with redirect_stdout(buf), redirect_stderr(buf):
            spec = importlib.util.spec_from_file_location("learner_handler", handler_path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            out["result"] = mod.handler(event, Ctx())
    except NotImplementedError:
        out["stub"] = True
    except Exception as e:  # noqa: BLE001
        out["crash"] = f"{type(e).__name__}: {e}"
    out["lines"] = [l for l in buf.getvalue().splitlines() if l.strip()]
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, default=str)


if __name__ == "__main__":
    main()
