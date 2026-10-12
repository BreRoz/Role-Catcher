"""Score the calibration roles and the negative examples, and show where the rubric needs tuning.

  python calibrate.py                   score calibration/*.txt and negative_examples/*.txt
  python calibrate.py --only calibration
  python calibrate.py --with-summaries  include calibration/summaries.md in the prompt (as production does)

Each file starts with "Company:", "Title:" and optionally "Location:" lines, then a blank line,
then the job description. A file containing "PASTE JOB DESCRIPTION HERE" is skipped.
Costs about 2 to 3 cents per role. Results are never cached, so every run is a fresh score."""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

from role_catcher import llm
from role_catcher.config import ROOT, load_config
from role_catcher.models import Job
from role_catcher.scorer import load_system_prompt, passes_gate, score_one

PLACEHOLDER = "PASTE JOB DESCRIPTION HERE"
TARGET = 75          # late-stage roles should reach this
NEGATIVE_MAX = 60    # negatives at or above this are a problem ("well under 75")


def read_job(path: Path) -> Job | None:
    text = path.read_text(encoding="utf-8")
    if PLACEHOLDER in text:
        return None
    head, _, body = text.partition("\n\n")
    fields = {}
    for line in head.splitlines():
        k, _, v = line.partition(":")
        fields[k.strip().lower()] = v.strip()
    return Job(ats="calibration", company=fields.get("company", path.stem), job_id=path.stem,
               title=fields.get("title", path.stem), location=fields.get("location", ""),
               url=fields.get("source", ""), description=body.strip(), comp=None)


def explain(r: dict) -> None:
    print(f"      seniority_match: {r['seniority_match']} | location_ok: {r['location_ok']}")
    for d in r["deal_breakers_hit"]:
        print(f"      ! deal-breaker: {d}")
    print(f"      reasoning: {r['reasoning']}")


def run_group(label: str, folder: str, client, config, system_prompt, is_positive: bool) -> tuple[float, int]:
    print(f"\n=== {label} ===")
    cost, problems = 0.0, 0
    for path in sorted((ROOT / folder).glob("*.txt")):
        job = read_job(path)
        if job is None:
            print(f"  SKIPPED  {path.name} (placeholder, paste the JD to include it)")
            continue
        try:
            r, c = score_one(client, config, job, system_prompt, purpose="calibration")
        except Exception as e:
            print(f"  ERROR    {path.name}: {e}")
            problems += 1
            continue
        cost += c
        score = r["fit_score"]
        bad = score < TARGET if is_positive else score >= NEGATIVE_MAX
        problems += bad
        gate = "gate PASS" if passes_gate(r, config["scoring"]["threshold"]) else "gate fail"
        print(f"  {score:>3}  {'BELOW TARGET' if bad and is_positive else 'TOO HIGH' if bad else 'ok':<12} "
              f"{job.company} | {job.title} ({gate})")
        if bad or not is_positive:
            explain(r)
    return cost, problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["calibration", "negatives"])
    ap.add_argument("--with-summaries", action="store_true")
    args = ap.parse_args()
    config = load_config()
    client = llm.get_client(config)
    system_prompt = load_system_prompt(include_calibration=args.with_summaries)
    total, problems = 0.0, 0
    if args.only != "negatives":
        c, p = run_group(f"Late-stage roles (target {TARGET}+)", "calibration", client, config, system_prompt, True)
        total, problems = total + c, problems + p
    if args.only != "calibration":
        c, p = run_group(f"Negative examples (should be well under {TARGET})", "negative_examples",
                         client, config, system_prompt, False)
        total, problems = total + c, problems + p
    print(f"\nCost this run: ${total:.4f}.  Problems: {problems}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
