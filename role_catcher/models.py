from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class Job:
    ats: str            # greenhouse | ashby | lever
    company: str
    job_id: str
    title: str
    location: str
    url: str
    description: str = ""
    posted_at: str | None = None   # ISO 8601 UTC, if the board provides it
    comp: str | None = None        # human-readable pay range, if listed
    board: str = ""                # "ats:slug" this job was fetched from
    new_board: bool = False        # board not yet baselined: its current jobs are not "new"

    @property
    def key(self) -> str:
        return f"{self.ats}:{self.company}:{self.job_id}"


@dataclass
class ScoredJob:
    job: Job
    score: int
    reasons: list = field(default_factory=list)
    company_verdict: dict = field(default_factory=dict)
