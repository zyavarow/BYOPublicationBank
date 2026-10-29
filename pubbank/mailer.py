"""Sending email: sign-in links and password-reset links.

Two ways, chosen by the host's environment variables (never in the code, so each installation uses its own account):

  Resend (over the web, works on any hosting plan, including Railway's Hobby plan where SMTP is blocked):
    RESEND_API_KEY      the key from the Resend dashboard (a "sending access" key for one domain is enough)
    PUBBANK_MAIL_FROM   the address people see, on a domain verified in Resend (for example login@mail.example.com)
    PUBBANK_MAIL_FROM_NAME   optional; the name shown next to it (default "BYO Publication Bank")

  Standard SMTP (any mail provider; not available on Railway's Hobby plan):
    PUBBANK_SMTP_HOST, PUBBANK_SMTP_PORT (default 587; 465 uses an encrypted connection from the start), PUBBANK_SMTP_USER,
    PUBBANK_SMTP_PASSWORD, PUBBANK_MAIL_FROM, PUBBANK_MAIL_FROM_NAME

  PUBBANK_BASE_URL    optional; the site's public address, used inside emailed links (otherwise the address the request arrived on is used)

If a Resend key is present it is used; otherwise SMTP, if configured. With neither, email is "not configured" and the app falls back to
links that an administrator sends by hand.
"""
import json
import os
import smtplib
import ssl
import urllib.error
import urllib.request
from email.message import EmailMessage
from email.utils import formataddr, parseaddr

RESEND_URL = "https://api.resend.com/emails"


def _from_address():
    return parseaddr(os.environ.get("PUBBANK_MAIL_FROM", ""))[1]


def _from_name():
    return os.environ.get("PUBBANK_MAIL_FROM_NAME", "").strip() or "BYO Publication Bank"


def provider():
    """'resend', 'smtp' or None: how email would be sent right now."""
    if not _from_address():
        return None
    if os.environ.get("RESEND_API_KEY"):
        return "resend"
    if os.environ.get("PUBBANK_SMTP_HOST"):
        return "smtp"
    return None


def configured():
    return provider() is not None


def base_url(request):
    return (os.environ.get("PUBBANK_BASE_URL") or str(request.base_url)).rstrip("/")


def _send_resend(to, subject, text):
    body = json.dumps({"from": formataddr((_from_name(), _from_address())), "to": [to], "subject": subject, "text": text}).encode("utf-8")
    req = urllib.request.Request(
        os.environ.get("PUBBANK_RESEND_URL") or RESEND_URL,          # the override exists only so tests can point at a local stand-in
        data=body, method="POST",
        headers={"Authorization": "Bearer " + os.environ["RESEND_API_KEY"], "Content-Type": "application/json", "User-Agent": "byo-publication-bank/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode("utf-8")).get("message") or ""
        except Exception:
            detail = ""
        raise RuntimeError(f"Resend refused the message (HTTP {e.code}): {detail or 'no details given'}") from None


def _send_smtp(to, subject, text):
    host = os.environ["PUBBANK_SMTP_HOST"]
    port = int(os.environ.get("PUBBANK_SMTP_PORT") or 587)
    user = os.environ.get("PUBBANK_SMTP_USER", "")
    password = os.environ.get("PUBBANK_SMTP_PASSWORD", "")
    msg = EmailMessage()
    msg["From"] = formataddr((_from_name(), _from_address()))
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text)
    ctx = ssl.create_default_context()
    server = smtplib.SMTP_SSL(host, port, timeout=20, context=ctx) if port == 465 else smtplib.SMTP(host, port, timeout=20)
    with server:
        if port != 465:
            server.starttls(context=ctx)
        if user:
            server.login(user, password)
        server.send_message(msg)


def send(to, subject, text, sender_name=None):
    """Send one plain-text email. Raises on failure (callers log it and show nothing that reveals whether an address has an account)."""
    if os.environ.get("PUBBANK_ENV", "").strip().lower() == "test":       # the Test site never emails real users
        allowed = {a.strip().lower() for a in os.environ.get("PUBBANK_TEST_EMAIL_ALLOW", "").replace(";", ",").split(",") if a.strip()}
        if to.strip().lower() not in allowed:
            raise RuntimeError("Test environment: that address is not in PUBBANK_TEST_EMAIL_ALLOW, so nothing was sent.")
    which = provider()
    if which == "resend":
        _send_resend(to, subject, text)
    elif which == "smtp":
        _send_smtp(to, subject, text)
    else:
        raise RuntimeError("Email is not configured on this installation.")
