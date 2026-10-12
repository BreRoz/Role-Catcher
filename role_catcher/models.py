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

    @property
    def key(self) -> str:
        return f"{self.ats}:{self.company}:{self.job_id}"


@dataclass
class ScoredJob:
    job: Job
    score: int
    reasons: list = field(default_factory=list)
    company_verdict: dict = field(default_factory=dict)
