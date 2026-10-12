from __future__ import annotations
import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
COMPANIES_FILE = ROOT / "companies.yaml"
PROFILE_FILE = ROOT / "profile.md"
CALIBRATION_DIR = ROOT / "calibration"
STATE_FILE = ROOT / "state" / "seen_jobs.json"

SCORE_THRESHOLD = 80
TIMEZONE = "America/Chicago"
RUN_HOURS_LOCAL = {7, 10, 13, 16, 19}   # Central-time hours the scheduled run is allowed to fire
DEFAULT_EMAIL_TO = "bre999@gmail.com"


def load_companies(include_paused: bool = False) -> list[dict]:
    data = yaml.safe_load(COMPANIES_FILE.read_text()) or {}
    companies = data.get("companies", [])
    if include_paused:
        return companies
    return [c for c in companies if c.get("status", "active") == "active"]


def env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None or value == "":
        raise RuntimeError(f"Missing required setting: {name}")
    return value
