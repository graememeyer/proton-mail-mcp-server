"""
Rendering helpers turning imap_tools MailMessage objects into the plain text the
tools return.
"""

__all__ = [
    "html_to_text",
    "format_summary",
    "format_detail",
    "message_body",
    "has_attachments_hint",
    "is_unread",
]

import html as html_module
import re
from datetime import datetime
from typing import Optional

from imap_tools import MailMessageFlags

from config import MAX_BODY_CHARS
from .date_utils import as_utc
from .ids import encode_id

# Elements whose contents are markup or styling, never prose.
_DROP_BLOCKS = re.compile(
    r"<(script|style|head)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL
)
# Tags that imply a line break once the markup is gone.
_BREAKS = re.compile(r"<\s*(br|/p|/div|/tr|/li|/h[1-6])\b[^>]*>", re.IGNORECASE)
_TAGS = re.compile(r"<[^>]+>")
_BLANK_RUNS = re.compile(r"\n{3,}")


def html_to_text(markup: str) -> str:
    """Flatten an HTML body to readable text.

    Deliberately dependency-free: Proton bodies arrive as ordinary MIME once
    Bridge has decrypted them, and a full HTML parser buys little for a
    text-only transport. Block-level tags become newlines so paragraphs and
    table rows don't run together.
    """
    if not markup:
        return ""

    text = _DROP_BLOCKS.sub(" ", markup)
    text = _BREAKS.sub("\n", text)
    text = _TAGS.sub("", text)
    text = html_module.unescape(text)
    text = "\n".join(line.rstrip() for line in text.splitlines())
    return _BLANK_RUNS.sub("\n\n", text).strip()


def message_body(msg) -> str:
    """Best available body text, preferring the plain-text alternative."""
    if msg.text and msg.text.strip():
        body = msg.text.strip()
    elif msg.html:
        body = html_to_text(msg.html)
    else:
        return "(no body content)"

    if len(body) > MAX_BODY_CHARS:
        return (
            body[:MAX_BODY_CHARS]
            + f"\n\n[... truncated, {len(body) - MAX_BODY_CHARS} more characters]"
        )
    return body


def fmt_date(when: Optional[datetime]) -> str:
    """Format a message date as an ISO-8601 UTC string (…Z).

    Everything is normalised to UTC first so a list of messages doesn't mix
    offsets (and naive dates) in a way that makes them hard to compare by eye.
    """
    if isinstance(when, datetime):
        return as_utc(when).strftime("%Y-%m-%dT%H:%M:%SZ")
    return str(when) if when else "unknown"


def _address(value) -> str:
    """Render an imap_tools EmailAddress as 'Name (addr)'."""
    if value is None:
        return "unknown"
    name = (getattr(value, "name", "") or "").strip()
    email = (getattr(value, "email", "") or "").strip() or "unknown"
    return f"{name} ({email})" if name else email


def _address_list(values, max_shown: Optional[int] = None) -> str:
    """Render a recipient tuple, optionally collapsing the tail."""
    if not values:
        return "None"
    items = list(values)
    if max_shown is not None and len(items) > max_shown:
        shown = [_address(v) for v in items[:max_shown]]
        shown.append(f"(+{len(items) - max_shown} more)")
        return ", ".join(shown)
    return ", ".join(_address(v) for v in items)


def is_unread(msg) -> bool:
    return MailMessageFlags.SEEN not in (msg.flags or ())


def has_attachments_hint(msg) -> bool:
    """Whether a message appears to carry attachments.

    Summaries are fetched headers-only, so the parsed attachment list is empty
    and the Content-Type header is all there is to go on. multipart/mixed is the
    structure a mail client uses for attachments; multipart/alternative (a
    text+HTML pair) and multipart/related (inline images) are not.
    """
    if msg.attachments:
        return True
    # imap_tools lowercases header keys; values are tuples (headers can repeat).
    values = (msg.headers or {}).get("content-type") or ()
    return any("multipart/mixed" in v.lower() for v in values)


def format_summary(msg, index: int, folder: str, include_recipients: bool = True) -> str:
    """One block per message, used by list_emails and search_emails."""
    unread = "[UNREAD] " if is_unread(msg) else ""
    attach = " [ATTACHMENTS]" if has_attachments_hint(msg) else ""

    out = (
        f"{index}. {unread}{fmt_date(msg.date)} - "
        f"From: {_address(msg.from_values)}{attach}\n"
    )
    if include_recipients:
        out += f"To: {_address_list(msg.to_values, max_shown=4)}\n"
        cc = _address_list(msg.cc_values, max_shown=4)
        if cc != "None":
            out += f"CC: {cc}\n"
    out += (
        f"Subject: {msg.subject or '(no subject)'}\n"
        f"ID: {encode_id(folder, msg.uid)}\n\n"
    )
    return out


def format_detail(msg, folder: str) -> str:
    """Full rendering used by read_email."""
    cc = _address_list(msg.cc_values)
    bcc = _address_list(msg.bcc_values)

    attachments = ""
    if msg.attachments:
        listed = ", ".join(
            f"{a.filename or '(unnamed)'} ({a.size} bytes)" for a in msg.attachments
        )
        attachments = f"Attachments: {len(msg.attachments)} - {listed}\n"

    return (
        f"From: {_address(msg.from_values)}\n"
        f"To: {_address_list(msg.to_values)}\n"
        + (f"CC: {cc}\n" if cc != "None" else "")
        + (f"BCC: {bcc}\n" if bcc != "None" else "")
        + f"Subject: {msg.subject or '(no subject)'}\n"
        f"Date: {fmt_date(msg.date)}\n"
        f"Folder: {folder}\n"
        f"ID: {encode_id(folder, msg.uid)}\n"
        f"Read: {'No' if is_unread(msg) else 'Yes'}\n"
        + attachments
        + f"\n{message_body(msg)}"
    )
