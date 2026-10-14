"""Sending email (password-reset links) through the organization's own mail server (standard SMTP).

Settings live in the host's environment variables, never in the code, so each installation uses its own mail account:
  PUBBANK_SMTP_HOST, PUBBANK_SMTP_PORT (default 587; 465 uses an encrypted connection from the start), PUBBANK_SMTP_USER,
  PUBBANK_SMTP_PASSWORD, PUBBANK_MAIL_FROM (the address people see), and optionally PUBBANK_BASE_URL (the site's public address,
  used inside emailed links; otherwise the address the request arrived on is used).
If the host or the From address is missing, email is "not configured" and the app falls back to administrator-made links.
"""
import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, parseaddr


def configured():
    return bool(os.environ.get("PUBBANK_SMTP_HOST") and parseaddr(os.environ.get("PUBBANK_MAIL_FROM", ""))[1])


def base_url(request):
    return (os.environ.get("PUBBANK_BASE_URL") or str(request.base_url)).rstrip("/")


def send(to, subject, text, sender_name="BYO Publication Bank"):
    """Send one plain-text email. Raises on failure (the caller logs it and shows nothing that reveals whether the address exists)."""
    host = os.environ["PUBBANK_SMTP_HOST"]
    port = int(os.environ.get("PUBBANK_SMTP_PORT") or 587)
    user = os.environ.get("PUBBANK_SMTP_USER", "")
    password = os.environ.get("PUBBANK_SMTP_PASSWORD", "")
    msg = EmailMessage()
    msg["From"] = formataddr((sender_name, parseaddr(os.environ["PUBBANK_MAIL_FROM"])[1]))
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text)
    ctx = ssl.create_default_context()
    if port == 465:
        server = smtplib.SMTP_SSL(host, port, timeout=20, context=ctx)
    else:
        server = smtplib.SMTP(host, port, timeout=20)
    with server:
        if port != 465:
            server.starttls(context=ctx)
        if user:
            server.login(user, password)
        server.send_message(msg)
