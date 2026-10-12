from __future__ import annotations
import smtplib
from email.message import EmailMessage

from .config import DEFAULT_EMAIL_TO, env
from .models import Job


def send_matches(matches: list[tuple[Job, int, str]]) -> None:
    """Email one digest of roles that scored at or above the threshold."""
    if not matches:
        return
    user = env("SMTP_USER")
    password = env("SMTP_APP_PASSWORD")
    to = env("EMAIL_TO", DEFAULT_EMAIL_TO)

    matches = sorted(matches, key=lambda m: m[1], reverse=True)
    msg = EmailMessage()
    msg["Subject"] = f"Role Catcher: {len(matches)} strong match{'es' if len(matches) != 1 else ''}"
    msg["From"] = user
    msg["To"] = to

    lines, html = [], []
    for job, score, reason in matches:
        lines.append(f"[{score}] {job.title} - {job.company} ({job.location})\n{job.url}\n{reason}\n")
        html.append(
            f"<p><b>[{score}] <a href=\"{job.url}\">{job.title}</a></b> - {job.company} "
            f"({job.location})<br>{reason}</p>"
        )
    msg.set_content("\n".join(lines))
    msg.add_alternative("<html><body>" + "".join(html) + "</body></html>", subtype="html")

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(user, password)
        smtp.send_message(msg)
