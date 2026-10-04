"""Sending email (invitations, password resets) over SMTP. Never raises into the caller:
send() returns whether the message went out, and the caller decides what to show instead.

MOCK_EMAIL=true keeps messages in SENT instead (tests and demos)."""
import logging
import smtplib
from email.message import EmailMessage

from app.config import settings

log = logging.getLogger(__name__)
SENT: list[dict] = []  # with MOCK_EMAIL: {"to", "subject", "text"}


def configured() -> bool:
    return settings.mock_email or bool(settings.smtp_host)


def send(to: str, subject: str, text: str) -> bool:
    if settings.mock_email:
        SENT.append({"to": to, "subject": subject, "text": text})
        return True
    if not settings.smtp_host:
        return False
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = settings.smtp_from, to, subject
    msg.set_content(text)
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
            if settings.smtp_starttls:
                smtp.starttls()
            if settings.smtp_user:
                smtp.login(settings.smtp_user, settings.smtp_password or "")
            smtp.send_message(msg)
        return True
    except (OSError, smtplib.SMTPException) as err:
        log.warning("Could not send email to %s: %s", to, err)  # never the password
        return False
