"""
send_email tool.

Submission goes through Bridge's local SMTP listener, which re-encrypts the
message for the recipients (PGP for Proton-to-Proton, plain SMTP otherwise) and
files a copy in Sent. There is nothing to do here beyond building a well-formed
MIME message.

The From address defaults to PROTON_SEND_FROM (or the Bridge user) but can be
overridden per message. Bridge accepts any address that belongs to the account
it is logged in as, so this is how a second address on the same account is used
without restarting the server. Bridge itself is the authority on ownership --
there is no way to enumerate the account's addresses over IMAP -- so an address
that is not on the account is caught at submission and reported as such.
"""

__all__ = [
    "handle_send_email",
    "split_addresses",
    "build_message",
    "normalise_sender",
    "InvalidSender",
]

import asyncio
import smtplib
from email.message import EmailMessage
from email.utils import formatdate, getaddresses, make_msgid, parseaddr
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


class InvalidSender(ValueError):
    """Raised when a caller-supplied From address is not a usable address."""


def normalise_sender(from_address: str) -> Tuple[str, str]:
    """Turn a caller-supplied From value into (header value, envelope address).

    Accepts either a bare address ("me@proton.me") or a display-name form
    ("Graeme <me@proton.me>"). The header keeps whatever was given; the envelope
    gets the bare address, because that is what Bridge matches against the
    addresses on the account. An empty value falls back to the configured
    default, which is the pre-existing behaviour.
    """
    candidate = (from_address or "").strip()
    if not candidate:
        return settings.send_from, settings.send_from

    if "," in candidate or len(getaddresses([candidate])) > 1:
        raise InvalidSender(
            f"from_address must be a single address, got {from_address!r}."
        )

    _, envelope = parseaddr(candidate)
    local, _, domain = envelope.partition("@")
    if not local or not domain:
        raise InvalidSender(
            f"{from_address!r} is not a valid email address. Pass a bare address "
            f'("you@proton.me") or a display-name form ("You <you@proton.me>").'
        )

    return candidate, envelope


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

    # sender may be "Name <addr@domain>", so the domain comes from the parsed
    # address rather than from the raw header value.
    domain = parseaddr(sender)[1].rpartition("@")[2] or None
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


def _describe_send_failure(
    exc: Exception, envelope_sender: str, from_address: str
) -> str:
    """Explain a rejected submission, naming the sender when one was overridden.

    Bridge does not refuse an address it does not own at MAIL FROM, where
    smtplib would raise SMTPSenderRefused; it accepts the envelope and then
    rejects the whole message at end-of-data with a 554 naming "sender or
    recipient". So every SMTPException is funnelled through here, and a
    caller-supplied From is called out as the first thing to check, since it is
    by far the likeliest cause when one was given.
    """
    if isinstance(exc, smtplib.SMTPResponseException):
        error = exc.smtp_error
        if isinstance(error, bytes):
            error = error.decode(errors="replace")
        detail = f"{exc.smtp_code} {error}"
    else:
        detail = str(exc)

    if from_address.strip():
        return (
            f"Proton Bridge rejected the message. Check the From address first: "
            f"{envelope_sender!r} must be an active address on the account Bridge "
            f"is logged in as ({settings.PROTON_BRIDGE_USER}); Bridge will not "
            f"send as an address outside that account. Bridge reported: {detail}"
        )
    return f"Proton Bridge rejected the message. Bridge reported: {detail}"


def _send_email(
    to: str,
    cc: str,
    bcc: str,
    subject: str,
    body: str,
    importance: str,
    reply_to_id: str,
    from_address: str = "",
) -> str:
    """Blocking body of send_email; runs in a worker thread."""
    sender, envelope_sender = normalise_sender(from_address)
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

    message = build_message(
        sender, to_list, cc_list, bcc_list, subject, body, importance, thread
    )

    with smtp_connection() as server:
        # Bcc recipients are passed to the envelope but deliberately not written
        # into a header, which is what makes them blind.
        try:
            server.send_message(
                message,
                from_addr=envelope_sender,
                to_addrs=to_list + cc_list + bcc_list,
            )
        except smtplib.SMTPException as exc:
            raise BridgeError(
                _describe_send_failure(exc, envelope_sender, from_address)
            ) from exc

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
    from_address: str = "",
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
        from_address: Address to send as. Must be an active address on the same
            Proton account Bridge is logged in as; any other address is refused
            by Bridge. Accepts a bare address ("you@proton.me") or a
            display-name form ("You <you@proton.me>"). Defaults to the
            configured sending address.

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
            _send_email, to, cc, bcc, subject, body, importance, reply_to_id,
            from_address,
        )
    except InvalidSender as e:
        return str(e)
    except BadMessageId as e:
        return str(e)
    except FolderNotFound as e:
        return str(e)
    except BridgeError as e:
        return str(e)
    except Exception as e:
        return f"Error sending email: {e}"
