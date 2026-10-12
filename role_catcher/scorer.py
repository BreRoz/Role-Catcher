from __future__ import annotations
"""Score a job 0-100 against profile.md + calibration/ using an AI model via OpenRouter.

STUB: implemented in a later step. score_job must return (score, reason).
"""
from .models import Job


def score_job(job: Job) -> tuple[int, str]:
    raise NotImplementedError("Scorer is built in a later step.")
