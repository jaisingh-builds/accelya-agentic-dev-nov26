#!/usr/bin/env python3
"""Day 4: run every check and print one scoreboard.

    python3 day4/tests/run_tests.py          # summary
    python3 day4/tests/run_tests.py -v       # plus the failure detail

Order: structure -> unit (service) -> logging (handler) -> functional (42-record file).
A skipped test is NOT a pass: it means a prerequisite is missing.
"""
import io
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SUITES = [("unit", "test_service"), ("logging", "test_logging"), ("functional", "test_functional")]


def main():
    verbose = "-v" in sys.argv
    sys.path.insert(0, str(HERE))
    st = subprocess.run([sys.executable, str(ROOT / "day4" / "scripts" / "check_structure.py")],
                        capture_output=True, text=True)
    print(f"  {'PASS' if st.returncode == 0 else 'FAIL'}  structure   {st.stdout.strip().splitlines()[-1]}")
    if st.returncode and verbose:
        print("\n".join("        " + l for l in st.stdout.splitlines() if "FAIL" in l))
    total = passed = 0
    for label, mod in SUITES:
        stream = io.StringIO()
        try:
            suite = unittest.defaultTestLoader.loadTestsFromName(mod)
        except Exception as e:                       # import error: the stub or a syntax error
            print(f"  FAIL  {label:<11} cannot import: {type(e).__name__}: {str(e)[:120]}")
            continue
        n = suite.countTestCases()
        res = unittest.TextTestRunner(stream=stream, verbosity=0).run(suite)
        ok_n = max(0, res.testsRun - len(res.failures) - len(res.errors) - len(res.skipped))
        bad = n - ok_n
        total += n
        passed += ok_n
        print(f"  {'PASS' if not bad else 'FAIL'}  {label:<11} {ok_n}/{n}")
        for test, why in res.skipped[:1]:
            print(f"        skipped: {why}")
        if verbose or bad:
            for test, tb in (res.failures + res.errors)[: (None if verbose else 3)]:
                last = [l for l in tb.strip().splitlines() if l.strip()][-1]
                print(f"        {test.id().split('.')[-1]}: {last[:150]}")
    ok = st.returncode == 0 and passed == total and total > 0
    print(f"\nDay 4: {passed}/{total} tests pass, structure {'OK' if st.returncode == 0 else 'NOT OK'}"
          f"  ->  {'READY TO DEPLOY' if ok else 'keep going'}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
