"""
send_email tool.

Submission goes through Bridge's local SMTP listener, which re-encrypts the
message for the recipients (PGP for Proton-to-Proton, plain SMTP otherwise) and
files a copy in Sent. There is nothing to do here beyond building a well-formed
MIME message.
"""

__all__ = ["handle_send_email", "split_addresses", "build_message"]

import asyncio
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import List, Optional, Tuple

from config import settings
from server import mcp
from .client import BridgeError, imap, smtp_connection
from .folders import FolderNotFound
from .ids import BadMessageId, decode_id

_IMPORTANCE = {
    "low": ("5", "Low"),
    "normal": ("3", "Normal"),
    "high": ("1", "High"),
}


def split_addresses(addresses: str) -> List[str]:
    """Split a comma-separated address string into individual addresses."""
    return [a.strip() for a in (addresses or "").split(",") if a.strip()]


def looks_like_html(body: str) -> bool:
    """Whether a body should be sent as HTML rather than plain text."""
    lowered = (body or "").lower()
    return "<html" in lowered or "<body" in lowered or "<div" in lowered


def build_message(
    sender: str,
    to: List[str],
    cc: List[str],
    bcc: List[str],
    subject: str,
    body: str,
    importance: str = "normal",
    thread: Optional[Tuple[str, str]] = None,
) -> EmailMessage:
    """Assemble the MIME message handed to SMTP.

    ``thread`` is an optional (message_id, references) pair from the message
    being replied to; setting In-Reply-To and References is what makes mail
    clients file the reply in the original conversation.
    """
    message = EmailMessage()
    message["From"] = sender
    message["To"] = ", ".join(to)
    if cc:
        message["Cc"] = ", ".join(cc)
    message["Subject"] = subject
    message["Date"] = formatdate(localtime=True)

    domain = sender.rpartition("@")[2] or None
    message["Message-ID"] = make_msgid(domain=domain)

    if importance.lower() != "normal":
        priority, label = _IMPORTANCE.get(importance.lower(), _IMPORTANCE["normal"])
        message["X-Priority"] = priority
        message["Importance"] = label

    if thread:
        in_reply_to, references = thread
        if in_reply_to:
            message["In-Reply-To"] = in_reply_to
            # References is the whole chain: the original's chain plus itself.
            message["References"] = f"{references} {in_reply_to}".strip()

    if looks_like_html(body):
        # A text/plain part is still required so that clients which won't render
        # HTML have something to show.
        message.set_content("This message is formatted in HTML.")
        message.add_alternative(body, subtype="html")
    else:
        message.set_content(body)

    return message


def _thread_headers(reply_to_id: str) -> Tuple[Optional[Tuple[str, str]], str]:
    """Fetch the Message-ID/References of the message being replied to.

    Returns the threading pair plus the original subject, so the caller can
    default the reply's subject to "Re: ...".
    """
    folder, uid = decode_id(reply_to_id)
    with imap(folder) as box:
        messages = list(box.fetch(uid_list=[uid], mark_seen=False, headers_only=True))

    if not messages:
        raise BadMessageId(
            f"Cannot reply: no message with ID {reply_to_id}. Re-run list_emails "
            f"or search_emails to get a current ID."
        )

    original = messages[0]
    headers = original.headers or {}
    message_id = (headers.get("message-id") or ("",))[0].strip()
    references = (headers.get("references") or ("",))[0].strip()
    return (message_id, references) if message_id else None, original.subject or ""


def _send_email(
    to: str,
    cc: str,
    bcc: str,
    subject: str,
    body: str,
    importance: str,
    reply_to_id: str,
) -> str:
    """Blocking body of send_email; runs in a worker thread."""
    to_list = split_addresses(to)
    cc_list = split_addresses(cc)
    bcc_list = split_addresses(bcc)

    thread = None
    if reply_to_id:
        thread, original_subject = _thread_headers(reply_to_id)
        if not subject and original_subject:
            subject = (
                original_subject
                if original_subject.lower().startswith("re:")
                else f"Re: {original_subject}"
            )

    sender = settings.send_from
    message = build_message(
        sender, to_list, cc_list, bcc_list, subject, body, importance, thread
    )

    with smtp_connection() as server:
        # Bcc recipients are passed to the envelope but deliberately not written
        # into a header, which is what makes them blind.
        server.send_message(message, from_addr=sender, to_addrs=to_list + cc_list + bcc_list)

    extra = (f" + {len(cc_list)} CC" if cc_list else "") + (
        f" + {len(bcc_list)} BCC" if bcc_list else ""
    )
    threaded = " (as a reply)" if thread else ""
    return (
        f"Email sent successfully{threaded}.\n\n"
        f"From: {sender}\n"
        f"Subject: {subject}\n"
        f"Recipients: {len(to_list)}{extra}\n"
        f"Body length: {len(body)} characters"
    )


@mcp.tool(name="send_email", title="Send email")
async def handle_send_email(
    to: str = "",
    cc: str = "",
    bcc: str = "",
    subject: str = "",
    body: str = "",
    importance: str = "normal",
    reply_to_id: str = "",
) -> str:
    """
    Send an email from the Proton Mail account

    The message is submitted to Proton Bridge, which encrypts it for the
    recipients and saves a copy to Sent.

    Args:
        to: Primary recipient addresses, comma-separated for multiple
        cc: Carbon copy addresses, comma-separated for multiple
        bcc: Blind carbon copy addresses, comma-separated for multiple
        subject: Subject line. Optional when reply_to_id is given, in which case
            it defaults to "Re: <original subject>"
        body: Message body. Sent as HTML if it looks like HTML, otherwise as
            plain text
        importance: "low", "normal" or "high" (default: "normal")
        reply_to_id: Message ID of an email to reply to, from list_emails or
            search_emails. Sets the threading headers so the reply appears in
            the original conversation. Recipients are NOT filled in
            automatically - pass them in `to` yourself.

    Returns:
        Confirmation with the sender, subject and recipient counts, or the
        reason sending failed
    """
    if not to:
        return "Recipient (to) is required."
    if not body:
        return "Body content is required."
    if not subject and not reply_to_id:
        return "Subject is required (or pass reply_to_id to inherit one)."

    try:
        return await asyncio.to_thread(
            _send_email, to, cc, bcc, subject, body, importance, reply_to_id
        )
    except BadMessageId as e:
        return str(e)
    except FolderNotFound as e:
        return str(e)
    except BridgeError as e:
        return str(e)
    except Exception as e:
        return f"Error sending email: {e}"
