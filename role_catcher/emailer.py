"""Step 5: send email via Gmail SMTP (section 7 defines the final layout)."""
import os
import smtplib
from email.message import EmailMessage

from .models import ScoredJob


def send_email(subject: str, body: str, config: dict) -> None:
    cfg = config["email"]
    msg = EmailMessage()
    msg["From"] = os.environ["SMTP_USER"]
    msg["To"] = os.environ.get("EMAIL_TO") or cfg["default_to"]
    msg["Subject"] = subject
    msg.set_content(body)
    with smtplib.SMTP_SSL(cfg["smtp_host"], cfg["smtp_port"]) as s:
        s.login(os.environ["SMTP_USER"], os.environ["SMTP_APP_PASSWORD"])
        s.send_message(msg)


def email_roles(roles: list[ScoredJob], config: dict) -> None:
    if not roles:
        return
    lines = [f"{r.job.title} at {r.job.company} (score {r.score})\n{r.job.url}" for r in roles]
    send_email(f"Role Catcher: {len(roles)} new match(es)", "\n\n".join(lines), config)
