#!/usr/bin/env python3
"""Day 5 scoreboard: the job's tests (given) + your Dockerfile (Lab 2).  python3 day5/tests/run_tests.py"""
import io
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
suite = unittest.defaultTestLoader.discover(str(Path(__file__).parent), pattern="test_*.py")
res = unittest.TextTestRunner(verbosity=1, stream=io.StringIO()).run(suite)
passed = res.testsRun - len(res.failures) - len(res.errors) - len(res.skipped)
for t, _ in res.failures + res.errors:
    print(f"  ✗ {t.id().split('.')[-1]}")
d = subprocess.run([sys.executable, str(ROOT / "day5/scripts/check_dockerfile.py")], capture_output=True, text=True)
dline = d.stdout.strip().splitlines()[-1]
print(f"\nDay 5: job tests {passed}/{res.testsRun}, {dline}  ->  "
      + ("READY TO BUILD" if passed == res.testsRun and d.returncode == 0 else "keep going"))
sys.exit(0 if passed == res.testsRun and d.returncode == 0 else 1)
