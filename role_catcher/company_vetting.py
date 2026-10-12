"""Step 4: company health vetting with 60-day cache (section 6)."""
from .models import ScoredJob


def vet_companies(scored: list[ScoredJob], config: dict) -> list[ScoredJob]:
    return []  # implemented in the vetting step
