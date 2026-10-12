from __future__ import annotations
"""Cheap pre-filters (title/location) applied before spending money on scoring.

STUB: implemented in a later step.
"""
from .models import Job


def passes_filters(job: Job) -> bool:
    return True
