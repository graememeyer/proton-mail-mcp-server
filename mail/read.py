"""
read_email tool.
"""

__all__ = ["handle_read_email"]

import asyncio

from server import mcp
from .client import BridgeError, imap
from .folders import FolderNotFound
from .formatting import format_detail
from .ids import BadMessageId, decode_id


def _read_email(message_id: str, mark_as_read: bool) -> str:
    """Blocking body of read_email; runs in a worker thread."""
    folder, uid = decode_id(message_id)

    with imap(folder) as box:
        resolved = box.folder.get()
        # uid_list skips the SEARCH round trip entirely and, with mark_seen
        # False, uses BODY.PEEK so reading doesn't silently change the flag.
        messages = list(
            box.fetch(uid_list=[uid], mark_seen=mark_as_read, bulk=False)
        )

    if not messages:
        return (
            f"No message with ID {message_id} in {resolved}. IMAP UIDs are "
            f"per-folder and change if the message is moved, so re-run "
            f"list_emails or search_emails to get a current ID."
        )

    return format_detail(messages[0], resolved)


@mcp.tool(name="read_email", title="Read email")
async def handle_read_email(id: str, mark_as_read: bool = False) -> str:
    """
    Read one email in full, including its body

    Args:
        id: The message ID from list_emails or search_emails, in "<folder>:<uid>"
            form (e.g. "INBOX:4821"). IDs are per-folder and become invalid if
            the message is moved.
        mark_as_read: Mark the message as read as a side effect of reading it
            (default: False, which leaves the unread flag untouched)

    Returns:
        Sender, recipients, subject, date, read status, attachment list and the
        full message body as text. HTML-only bodies are flattened to text.
    """
    try:
        return await asyncio.to_thread(_read_email, id, mark_as_read)
    except BadMessageId as e:
        return str(e)
    except FolderNotFound as e:
        return str(e)
    except BridgeError as e:
        return str(e)
    except Exception as e:
        return f"Error reading email: {e}"
