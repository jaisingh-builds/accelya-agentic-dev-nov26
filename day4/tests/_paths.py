"""Shared paths and imports for the Day 4 tests (run from anywhere)."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "day4" / "src" / "processor"
DATA = ROOT / "day3" / "data"
FILE42 = DATA / "ticket_issue_AI_2026-11-25.csv"
RESEND = DATA / "ticket_issue_AI_2026-11-25_resend.csv"
PKG_RULES = SRC / "rules" / "validation_rules.json"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("LEARNER_ID", "test")
os.environ.setdefault("POWERTOOLS_LOG_LEVEL", "INFO")
