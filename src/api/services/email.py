"""Outgoing email over SMTP, configured with the SMTP_* variables in .env.

With no SMTP_HOST set, messages are printed to the server log instead of
sent, so the password reset flow can still be used in local development.
"""
import os
import smtplib
from email.message import EmailMessage


def smtp_configured() -> bool:
    return bool(os.getenv("SMTP_HOST"))


def send_email(to: str, subject: str, body: str) -> None:
    if not smtp_configured():
        print(f"SMTP_HOST is not set; email to {to} not sent.\nSubject: {subject}\n\n{body}")
        return

    message = EmailMessage()
    message["From"] = os.getenv("SMTP_FROM") or os.getenv("SMTP_USERNAME", "")
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)

    host = os.getenv("SMTP_HOST")
    port = int(os.getenv("SMTP_PORT", "587"))
    username = os.getenv("SMTP_USERNAME")
    password = os.getenv("SMTP_PASSWORD")
    # Port 465 is implicit TLS; anything else upgrades with STARTTLS.
    if port == 465:
        server = smtplib.SMTP_SSL(host, port, timeout=30)
    else:
        server = smtplib.SMTP(host, port, timeout=30)
    with server:
        if port != 465:
            server.starttls()
        if username:
            server.login(username, password or "")
        server.send_message(message)
