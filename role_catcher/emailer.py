"""Step 5: email via Gmail SMTP (section 7).

One email per qualifying role, sent as soon as it is found, plus a weekly health
email (built in health.py). Emails are described as a list of simple blocks and
rendered to both plain text and HTML, so the two never drift apart.

Block kinds: ("apply", url), ("title", text), ("p", text), ("warn", text),
("h", text), ("kv", [(label, value)]), ("bullets", [text or (text, url)]),
("links", [url])."""
from __future__ import annotations
import html
import json
import logging
import os
import re
import smtplib
from dataclasses import dataclass, field
from datetime import datetime
from email.message import EmailMessage
from zoneinfo import ZoneInfo

from .config import ROOT
from .models import ScoredJob

log = logging.getLogger("role_catcher.emailer")

EMAIL_LOG = ROOT / "logs" / "emails.jsonl"


def clean(text) -> str:
    """House style for everything the agent writes: no em dashes, never the word
    'intersection'. Applied to all generated and model-written copy."""
    s = str(text)
    s = re.sub(r"\s*—\s*", ", ", s)    # em dash
    s = s.replace("–", "-")             # en dash (ranges)
    s = re.sub(r"\bintersections?\b", lambda m: "Overlap" if m.group()[0].isupper() else "overlap", s, flags=re.I)
    return s.strip()


# ---------------------------------------------------------------- rendering

def render(blocks: list) -> tuple[str, str]:
    """Blocks to (plain text, HTML)."""
    text: list[str] = []
    out = ['<div style="font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;'
           'font-size:15px;line-height:1.5;color:#1a1a1a;max-width:640px">']
    for kind, val in blocks:
        if kind == "apply":
            text += [f"APPLY: {val}", ""]
            out.append(f'<p style="margin:0 0 20px"><a href="{html.escape(val)}" style="font-size:22px;'
                       f'font-weight:700;color:#0b5fff">Apply here</a><br>'
                       f'<span style="font-size:13px;color:#555">{html.escape(val)}</span></p>')
        elif kind == "title":
            text += [clean(val), ""]
            out.append(f'<h2 style="margin:0 0 12px;font-size:18px">{html.escape(clean(val))}</h2>')
        elif kind == "p":
            text += [clean(val), ""]
            out.append(f'<p style="margin:0 0 14px">{html.escape(clean(val))}</p>')
        elif kind == "warn":
            text += [clean(val), ""]
            out.append(f'<p style="margin:0 0 14px;padding:8px 10px;background:#fff3cd;'
                       f'border-left:4px solid #d39e00"><strong>{html.escape(clean(val))}</strong></p>')
        elif kind == "h":
            text += [clean(val).upper()]
            out.append(f'<h3 style="margin:18px 0 6px;font-size:13px;letter-spacing:.05em;'
                       f'text-transform:uppercase;color:#555">{html.escape(clean(val))}</h3>')
        elif kind == "kv":
            for k, v in val:
                text.append(f"{k}: {clean(v)}")
            text.append("")
            rows = "".join(f'<tr><td style="padding:2px 12px 2px 0;color:#555;vertical-align:top">'
                           f'{html.escape(k)}</td><td>{html.escape(clean(v))}</td></tr>' for k, v in val)
            out.append(f'<table style="border-collapse:collapse;margin:0 0 14px">{rows}</table>')
        elif kind == "bullets":
            items = []
            for it in val:
                label, url = (it, None) if isinstance(it, str) else it
                text.append(f"- {clean(label)}" + (f" ({url})" if url else ""))
                body = html.escape(clean(label))
                if url:
                    body += f' (<a href="{html.escape(url)}">link</a>)'
                items.append(f"<li style=\"margin-bottom:4px\">{body}</li>")
            text.append("")
            out.append(f'<ul style="margin:0 0 14px;padding-left:20px">{"".join(items)}</ul>')
        elif kind == "links":
            for u in val:
                text.append(u)
            text.append("")
            out.append('<p style="margin:0 0 14px;font-size:13px">' + "<br>".join(
                f'<a href="{html.escape(u)}">{html.escape(u)}</a>' for u in val) + "</p>")
    out.append("</div>")
    return "\n".join(text).strip() + "\n", "\n".join(out)


def send_email(subject: str, body: str, config: dict, html_body: str | None = None) -> None:
    cfg = config["email"]
    msg = EmailMessage()
    msg["From"] = os.environ["SMTP_USER"]
    msg["To"] = os.environ.get("EMAIL_TO") or cfg["default_to"]
    msg["Subject"] = clean(subject).replace("\n", " ")
    msg.set_content(body)
    if html_body:
        msg.add_alternative(html_body, subtype="html")
    with smtplib.SMTP_SSL(cfg["smtp_host"], cfg["smtp_port"]) as s:
        s.login(os.environ["SMTP_USER"], os.environ["SMTP_APP_PASSWORD"])
        s.send_message(msg)


# ---------------------------------------------------------------- role email

def _date(iso: str | None) -> str | None:
    try:
        d = datetime.fromisoformat((iso or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return f"{d:%b} {d.day}, {d.year}"


def role_subject(s: ScoredJob) -> str:
    return clean(f"[Role Catcher] {s.score}% | {s.job.title} | {s.job.company}")


def role_blocks(s: ScoredJob) -> list:
    j, r = s.job, s.result
    meta = [("Company", j.company), ("Location", j.location or "not listed")]
    comp = r.get("comp_if_listed") or j.comp
    if comp:
        meta.append(("Comp", comp))
    posted = _date(j.posted_at)
    if posted:
        meta.append(("Posted", posted))
    blocks: list = [("apply", j.url), ("title", j.title), ("kv", meta)]
    blocks.append(("kv", [("Role fit", f"{s.score}%")]))
    return blocks


def build_role_email(s: ScoredJob) -> tuple[str, str, str]:
    text, html_body = render(role_blocks(s))
    return role_subject(s), text, html_body


# ---------------------------------------------------------------- sending

@dataclass
class EmailRun:
    sent: list = field(default_factory=list)      # ScoredJob emailed
    failed: list = field(default_factory=list)    # send raised; retry next run
    capped: list = field(default_factory=list)    # over max_emails_per_day; retry tomorrow
    cap_hit: bool = False


def emails_sent_today(tz_name: str, path=None) -> int:
    path = path or EMAIL_LOG
    if not path.exists():
        return 0
    tz = ZoneInfo(tz_name)
    today = datetime.now(tz).date()
    n = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            if datetime.fromisoformat(json.loads(line)["ts"]).astimezone(tz).date() == today:
                n += 1
        except (ValueError, KeyError):
            continue
    return n


def log_email(s: ScoredJob, subject: str) -> None:
    EMAIL_LOG.parent.mkdir(exist_ok=True)
    with open(EMAIL_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": datetime.now().astimezone().isoformat(timespec="seconds"),
                            "key": s.job.key, "company": s.job.company, "title": s.job.title,
                            "score": s.score, "subject": subject}) + "\n")


def email_roles(roles: list[ScoredJob], config: dict, dry_run: bool = False, sender=None) -> EmailRun:
    """Send one email per role, best score first. A send failure is logged loudly
    and that role is left for the next run; it never stops the others. Honors
    limits.max_emails_per_day."""
    sender = sender or send_email
    run = EmailRun()
    cap = config["limits"]["max_emails_per_day"]
    already = 0 if dry_run else emails_sent_today(config["timezone"])
    for s in sorted(roles, key=lambda r: -r.score):
        if already + len(run.sent) >= cap:
            if not run.cap_hit:
                print(f"CAP HIT: daily email cap of {cap} reached; remaining roles wait until tomorrow.")
                log.warning("daily email cap of %d reached", cap)
            run.cap_hit = True
            run.capped.append(s)
            continue
        subject, text, html_body = build_role_email(s)
        if dry_run:
            print(f"--- would send ---\nSubject: {subject}\n\n{text}")
            run.sent.append(s)
            continue
        try:
            sender(subject, text, config, html_body)
        except Exception as e:
            run.failed.append(s)
            log.error("EMAIL SEND FAILED for %s (%s): %s", s.job.title, s.job.company, e)
            print(f"EMAIL SEND FAILED: {s.job.company} | {s.job.title}: {e} (will retry next run)")
            continue
        log_email(s, subject)
        run.sent.append(s)
        print(f"emailed: {subject}")
    return run
