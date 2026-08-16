"""
Stable-ish message identifiers.

IMAP has no mailbox-wide message id: a UID is only meaningful inside one folder,
and is invalidated wholesale if the server bumps the folder's UIDVALIDITY. Tools
therefore hand out a composite ``<folder>:<uid>`` string, so read/move/mark can
find the message again without the caller having to track which folder it came
from.

The UID is always digits, so splitting on the *last* colon keeps working for
Proton's namespaced folder names (``Folders/Work:1234``).
"""

__all__ = ["encode_id", "decode_id", "BadMessageId"]

from typing import Tuple


class BadMessageId(ValueError):
    """Raised when a caller-supplied message id isn't in <folder>:<uid> form."""


def encode_id(folder: str, uid: str) -> str:
    """Build the opaque id handed back to callers."""
    return f"{folder}:{uid}"


def decode_id(message_id: str) -> Tuple[str, str]:
    """Split an id back into (folder, uid), validating the shape.

    Returns the folder exactly as it was encoded, which is the resolved Bridge
    folder name, so it round-trips through pick_folder unchanged.
    """
    raw = (message_id or "").strip()
    if not raw:
        raise BadMessageId("A message id is required.")

    folder, sep, uid = raw.rpartition(":")
    if not sep or not folder or not uid.isdigit():
        raise BadMessageId(
            f"{message_id!r} is not a valid message id. Ids look like "
            f"'INBOX:1234' and come from list_emails or search_emails."
        )
    return folder, uid
