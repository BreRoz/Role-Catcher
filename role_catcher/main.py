import os
from datetime import datetime
from zoneinfo import ZoneInfo

from . import state
from .company_vetting import vet_companies
from .config import load_config, load_yaml
from .discovery import discover
from .emailer import email_roles
from .filters import prefilter
from .scorer import score_jobs


def should_run(config: dict) -> bool:
    """Scheduled runs fire at both daylight and standard UTC offsets; only the
    ones landing on a configured Central hour proceed. Manual runs always go."""
    if os.environ.get("GITHUB_EVENT_NAME") != "schedule":
        return True
    hour = datetime.now(ZoneInfo(config["timezone"])).hour
    return hour in config["run_hours_local"]


def main() -> None:
    config = load_config()
    if not should_run(config):
        print("Not a configured run hour; exiting.")
        return

    seen = state.load("seen_jobs.json", {})

    jobs = discover(config)
    jobs = [j for j in jobs if j.key not in seen]
    jobs = prefilter(jobs, load_yaml("filters.yaml"))
    scored = score_jobs(jobs, config)
    scored = [s for s in scored if s.score >= config["scoring"]["threshold"]]
    approved = vet_companies(scored, config)
    email_roles(approved, config)

    for j in jobs:  # remember everything evaluated so it is never processed twice
        seen[j.key] = datetime.now().date().isoformat()
    state.save("seen_jobs.json", seen)


if __name__ == "__main__":
    main()
