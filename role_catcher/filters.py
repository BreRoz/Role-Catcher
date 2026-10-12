"""Step 2: title, seniority, location pre-filter in code, no AI (section 4)."""
from .models import Job


def prefilter(jobs: list[Job], rules: dict) -> list[Job]:
    return jobs  # implemented in the filter step
