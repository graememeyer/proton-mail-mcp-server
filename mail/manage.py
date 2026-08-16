"""
mark_email / move_email tools -- the small state changes that make the read
tools useful in practice.

There is deliberately no permanent-delete tool: move_email to "trash" is the
reversible equivalent, and Proton empties Trash on its own schedule.
"""

__all__ = ["handle_mark_email", "handle_move_email"]

import asyncio

from imap_tools import MailMessageFlags

from server import mcp
from .client import BridgeError, imap
from .folders import FolderNotFound, pick_folder
from .ids import BadMessageId, decode_id


def _exists(box, uid: str) -> bool:
    """Whether a UID is still present in the selected folder.

    fetch() is a generator, so it has to be drained before it says anything
    about whether the message is there.
    """
    return bool(list(box.fetch(uid_list=[uid], mark_seen=False, headers_only=True)))


def _mark_email(message_id: str, read: bool) -> str:
    """Blocking body of mark_email; runs in a worker thread."""
    folder, uid = decode_id(message_id)

    with imap(folder) as box:
        resolved = box.folder.get()
        if not _exists(box, uid):
            return f"No message with ID {message_id} in {resolved}."
        box.flag([uid], MailMessageFlags.SEEN, read)

    state = "read" if read else "unread"
    return f"Marked {message_id} as {state}."


@mcp.tool(name="mark_email", title="Mark email read or unread")
async def handle_mark_email(id: str, read: bool = True) -> str:
    """
    Mark an email as read or unread

    Args:
        id: The message ID from list_emails or search_emails ("<folder>:<uid>")
        read: True to mark as read, False to mark as unread (default: True)

    Returns:
        Confirmation of the new state, or the reason it failed
    """
    try:
        return await asyncio.to_thread(_mark_email, id, read)
    except BadMessageId as e:
        return str(e)
    except FolderNotFound as e:
        return str(e)
    except BridgeError as e:
        return str(e)
    except Exception as e:
        return f"Error marking email: {e}"


def _move_email(message_id: str, destination: str) -> str:
    """Blocking body of move_email; runs in a worker thread."""
    folder, uid = decode_id(message_id)

    with imap(folder) as box:
        resolved = box.folder.get()
        available = [f.name for f in box.folder.list()]
        target = pick_folder(destination, available)

        if target == resolved:
            return f"Message {message_id} is already in {target}."
        if not _exists(box, uid):
            return f"No message with ID {message_id} in {resolved}."

        box.move([uid], target)

    # The destination assigns its own UID, so the old ID is now dead. Say so
    # rather than letting the caller retry a stale ID.
    return (
        f"Moved {message_id} from {resolved} to {target}. Its ID has changed - "
        f"list or search {target} to get the new one."
    )


@mcp.tool(name="move_email", title="Move email to another folder")
async def handle_move_email(id: str, destination: str) -> str:
    """
    Move an email to another folder

    Moving to "trash" is how you delete a message; Proton clears Trash on its
    own schedule and the message can be recovered until then.

    Args:
        id: The message ID from list_emails or search_emails ("<folder>:<uid>")
        destination: Target folder. Accepts system names ("archive", "trash",
            "spam", "inbox"), your own folders and labels by plain name
            ("Work"), or the full Bridge path ("Folders/Work")

    Returns:
        Confirmation of the move, or the reason it failed
    """
    if not destination:
        return "A destination folder is required."

    try:
        return await asyncio.to_thread(_move_email, id, destination)
    except BadMessageId as e:
        return str(e)
    except FolderNotFound as e:
        return str(e)
    except BridgeError as e:
        return str(e)
    except Exception as e:
        return f"Error moving email: {e}"
