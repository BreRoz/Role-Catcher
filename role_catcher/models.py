from __future__ import annotations
from dataclasses import dataclass


@dataclass
class Job:
    company: str
    job_id: str          # unique within the company
    title: str
    location: str
    url: str
    description: str = ""

    @property
    def key(self) -> str:
        return f"{self.company}::{self.job_id}"
