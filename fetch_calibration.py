"""One-time helper: save the calibration job descriptions from the Greenhouse API
into calibration/*.txt.  Usage: python fetch_calibration.py
If a posting has been taken down (404), paste its text into the file by hand."""
import html
import os
import sys

from role_catcher.ats import BoardNotFound, get_json, html_to_text

ROLES = [  # (file name, board slug, job id, why it was a fit: summary goes in calibration/summaries.md later)
    ("figma", "figma", "6013495004"),
    ("gong_gtm_ai_architect", "gongio", "4693862006"),
    ("manychat_ai_marketing_automation_manager", "manychat", "8616852002"),
    ("everlaw", "everlaw", "4690537006"),
]
PLACEHOLDER = "PASTE JOB DESCRIPTION HERE"
API = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{id}"


def write_placeholder(name: str, slug: str) -> None:
    with open(f"calibration/{name}.txt", "w", encoding="utf-8") as f:
        f.write(f"{PLACEHOLDER}\n(The {slug} posting was taken down. Replace this whole file with the "
                f"job description text, keeping a Company: and Title: line at the top. "
                f"calibrate.py skips this file until the marker line above is gone.)\n")


def main() -> int:
    missing = 0
    for name, slug, job_id in ROLES:
        try:
            j = get_json(API.format(slug=slug, id=job_id))
        except BoardNotFound:
            print(f"{name}: 404 (posting removed). Paste the JD into calibration/{name}.txt by hand.")
            missing += 1
            if not os.path.exists(f"calibration/{name}.txt"):
                write_placeholder(name, slug)
            continue
        body = html_to_text(html.unescape(j.get("content") or ""))
        header = (f"Company: {j.get('company_name') or slug}\nTitle: {j.get('title')}\n"
                  f"Location: {(j.get('location') or {}).get('name', '')}\nSource: {j.get('absolute_url')}\n\n")
        with open(f"calibration/{name}.txt", "w", encoding="utf-8") as f:
            f.write(header + body + "\n")
        print(f"{name}: saved ({len(body)} chars) - {j.get('title')}")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
