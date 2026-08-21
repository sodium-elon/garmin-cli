"""Fetch the Garmin email-OTP (MFA) code from Gmail over IMAP.

Port of the essential logic from garmin-mcp's mfa-email.ts:

- Trust only messages with envelope sender ``alerts@account.garmin.com`` and
  subject ``Your Security Passcode``.
- Verify Gmail's topmost Authentication-Results header (receiver-controlled,
  prepended at ingress) shows spf=pass and dkim=pass for garmin.com.
- Extract the 6-digit code from the rendered text near the phrase
  "one-time code for your account", after stripping comments, script/style
  blocks, and HTML tags so digits hidden in CSS cannot be mistaken for the code.
- Arm before login: remember UIDNEXT so only mail that arrives *after* the
  login attempt started is considered.
"""

from __future__ import annotations

import email
import html
import imaplib
import logging
import re
import time
from email.header import decode_header, make_header
from email.message import Message

logger = logging.getLogger(__name__)

IMAP_HOST = "imap.gmail.com"
GARMIN_SENDER = "alerts@account.garmin.com"
GARMIN_SUBJECT = "Your Security Passcode"
CODE_PATTERN = re.compile(r"one-time code for your account\D{0,120}(\d{6})", re.IGNORECASE)
DEFAULT_TIMEOUT_SECONDS = 180.0
POLL_SECONDS = 5.0


class MfaError(RuntimeError):
    """Could not obtain an OTP code with the available material."""


def _decode_str(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        logger.debug("Could not decode email header; using raw value", exc_info=True)
        return str(value)


def _render_text(message: Message) -> str:
    """Best-effort rendered visible text of a MIME message."""
    parts: list[str] = []
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    parts.append(payload.decode("utf-8", errors="replace"))
            elif part.get_content_type() == "text/html":
                payload = part.get_payload(decode=True)
                if payload:
                    parts.append(_strip_html(payload.decode("utf-8", errors="replace")))
    else:
        payload = message.get_payload(decode=True)
        if payload:
            text = payload.decode("utf-8", errors="replace")
            if message.get_content_type() == "text/html":
                text = _strip_html(text)
            parts.append(text)
    return " ".join(parts)


def _strip_html(raw: str) -> str:
    # Remove comments and non-rendered blocks, then tags.
    visible = re.sub(r"<!--[\s\S]*?-->", " ", raw)
    visible = re.sub(
        r"<(?:head|style|script|template|noscript)\b[^>]*>[\s\S]*?</(?:head|style|script|template|noscript)\s*>",
        " ",
        visible,
        flags=re.IGNORECASE,
    )
    visible = re.sub(r"<[^>]+>", " ", visible)
    return html.unescape(visible)


def extract_code(rendered: str) -> str | None:
    r"""Return the unique 6-digit code, or None if absent/ambiguous.

    Whitespace runs are collapsed first: HTML table-cell layout can put more
    than 120 chars of newline/indent whitespace between the phrase and the
    code, which would defeat the \D{0,120} window (garmin-mcp's renderer
    collapses whitespace for the same reason).
    """
    collapsed = re.sub(r"\s+", " ", rendered)
    codes = {m.group(1) for m in CODE_PATTERN.finditer(collapsed)}
    return codes.pop() if len(codes) == 1 else None


def _auth_results_ok(message: Message) -> bool:
    """Trust only the topmost Authentication-Results header (Gmail ingress)."""
    raw = message.get("Authentication-Results")
    if not raw:
        return False
    header = " ".join(str(raw).split())
    lowered = header.lower()
    return "spf=pass" in lowered and "dkim=pass" in lowered and "garmin.com" in lowered


def _message_matches(message: Message) -> bool:
    sender = _decode_str(message.get("From", "")).lower()
    subject = _decode_str(message.get("Subject", ""))
    return GARMIN_SENDER in sender and subject.strip() == GARMIN_SUBJECT


def _uidnext(mail: imaplib.IMAP4_SSL) -> int:
    status, data = mail.status("INBOX", "(UIDNEXT)")
    if status != "OK" or not data:
        raise MfaError("Could not read INBOX UIDNEXT")
    # b'INBOX (UIDNEXT 12345)'
    match = re.search(rb"UIDNEXT\s+(\d+)", data[0])
    if not match:
        raise MfaError("Malformed UIDNEXT response")
    return int(match.group(1))


class OtpWaiter:
    """Armed before login; polls Gmail for the OTP email triggered by login."""

    def __init__(self, email_address: str, app_password: str):
        self._email = email_address
        self._password = app_password
        self._uidnext: int | None = None

    def _connect(self) -> imaplib.IMAP4_SSL:
        mail = imaplib.IMAP4_SSL(IMAP_HOST)
        mail.login(self._email, self._password)
        return mail

    def arm(self) -> None:
        """Record the current UIDNEXT so older mail is never considered."""
        mail = self._connect()
        try:
            self._uidnext = _uidnext(mail)
        finally:
            try:
                mail.logout()
            except Exception:
                logger.debug("IMAP logout failed during OTP arming", exc_info=True)

    def wait(self, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> str:
        """Poll until a matching fresh OTP email arrives; return the code."""
        if self._uidnext is None:
            self.arm()
        deadline = time.monotonic() + timeout
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                return self._collect()
            except MfaError as exc:
                last_error = exc
                logger.debug("OTP not ready yet: %s", exc)
            time.sleep(POLL_SECONDS)
        raise MfaError(
            f"No Garmin OTP email arrived within {timeout:.0f}s (last: {last_error})"
        )

    def _collect(self) -> str:
        mail = self._connect()
        try:
            mail.select("INBOX", readonly=True)
            status, data = mail.uid("search", None, f"UID {self._uidnext}:*")
            if status != "OK" or not data or not data[0]:
                raise MfaError("UID search failed")
            uids = data[0].split()
            for uid in reversed(uids):  # newest first
                status, fetched = mail.uid("fetch", uid, "(BODY.PEEK[])")
                if status != "OK" or not fetched:
                    continue
                raw = next((b for b in fetched if isinstance(b, tuple)), None)
                if raw is None:
                    continue
                message = email.message_from_bytes(raw[1])
                if not _message_matches(message):
                    continue
                if not _auth_results_ok(message):
                    continue
                code = extract_code(_render_text(message))
                if code:
                    return code
            raise MfaError("No matching verified OTP email in the new range yet")
        finally:
            try:
                mail.logout()
            except Exception:
                logger.debug("IMAP logout failed after OTP collection", exc_info=True)


def fetch_code(email_address: str, app_password: str, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> str:
    """One-shot helper: arm + wait."""
    waiter = OtpWaiter(email_address, app_password)
    waiter.arm()
    return waiter.wait(timeout)
