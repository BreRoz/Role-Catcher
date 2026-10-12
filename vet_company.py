"""Try company vetting by hand:  python vet_company.py "Company Name" [more names...] [--cached]
Runs a live web-search research call (costs about a cent or two) and prints the verdict."""
import argparse
import json
import sys

from role_catcher import llm, state
from role_catcher.company_vetting import research
from role_catcher.config import load_config
from role_catcher.state import verdict_is_fresh


def show(v: dict) -> None:
    s = v.get("signals") or {}
    print(f"\n{v['company']}: {v['verdict'].upper()}  (trajectory: {v['trajectory']}, confidence: {v['confidence']})")
    for r in v.get("reasons") or []:
        print(f"  reason: {r}")
    for k, label in [("headcount_trend", "Headcount"), ("hiring_trend", "Hiring"),
                     ("funding_or_revenue_stage", "Funding"), ("recent_layoffs", "Layoffs"),
                     ("employee_sentiment", "Sentiment"), ("reputation_notes", "Reputation")]:
        print(f"  {label}: {s.get(k, '')}")
    print(f"  Model's own verdict: {v.get('model_verdict')} (the pass/flag/block shown above is computed from config.yaml rules)")
    for u in v.get("sources") or []:
        print(f"  source: {u}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("companies", nargs="+")
    ap.add_argument("--cached", action="store_true", help="use state/company_verdicts.json if fresh")
    ap.add_argument("--json", action="store_true", help="print raw JSON")
    args = ap.parse_args()
    config = load_config()
    cache = state.load("company_verdicts.json", {}) if args.cached else {}
    client = llm.get_client(config)
    for name in args.companies:
        hit = cache.get(name.lower())
        if hit and verdict_is_fresh(hit, config["vetting"]["cache_days"]):
            v = hit
        else:
            v, cost = research(client, config, name, purpose="vetting_manual")
            print(f"(cost ${cost:.4f})", file=sys.stderr)
        print(json.dumps(v, indent=2) if args.json else "", end="")
        if not args.json:
            show(v)
    return 0


if __name__ == "__main__":
    sys.exit(main())
