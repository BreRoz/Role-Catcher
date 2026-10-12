"""Step 3: LLM role-fit scoring against profile.md (section 5)."""
from __future__ import annotations
from .models import Job, ScoredJob


def score_jobs(jobs: list[Job], config: dict) -> list[ScoredJob]:
    return []  # implemented in the scoring step
