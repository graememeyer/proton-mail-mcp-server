"""
list_emails / list_folders tools.
"""

__all__ = ["handle_list_emails", "handle_list_folders"]

import asyncio

from config import DEFAULT_PAGE_SIZE, MAX_RESULT_COUNT
from server import mcp
from .client import BridgeError, imap
from .date_utils import DateParseError, build_date_criteria
from .folders import FolderNotFound, describe_folders, is_sent_like
from .formatting import format_summary
from .query import fetch_summaries


def _list_emails(
    folder: str,
    count: int,
    received_after: str,
    received_before: str,
    unread_only: bool,
    include_recipients: bool,
) -> str:
    """Blocking body of list_emails; runs in a worker thread."""
    criteria = build_date_criteria(
        received_after, received_before, sent_like=is_sent_like(folder)
    )

    with imap(folder) as box:
        resolved = box.folder.get()
        messages = fetch_summaries(
            box,
            resolved,
            count,
            criteria=criteria,
            received_after=received_after,
            received_before=received_before,
            unread_only=unread_only,
        )

    window = ""
    if received_after or received_before:
        window = (
            f" between {received_after or 'the beginning'} "
            f"and {received_before or 'now'}"
        )
    scope = " unread" if unread_only else ""

    if not messages:
        return f"No{scope} emails found in {resolved}{window}."

    body = "".join(
        format_summary(m, i, resolved, include_recipients=include_recipients)
        for i, m in enumerate(messages, 1)
    )
    truncated = " (truncated to the requested count)" if len(messages) == count else ""
    return (
        f"Found {len(messages)}{scope} emails in {resolved}{window}{truncated}:\n\n{body}"
    )


@mcp.tool(name="list_emails", title="List emails")
async def handle_list_emails(
    folder: str = "inbox",
    count: int = DEFAULT_PAGE_SIZE,
    received_after: str = "",
    received_before: str = "",
    unread_only: bool = False,
    include_recipients: bool = True,
) -> str:
    """
    List the most recent emails in a Proton Mail folder, newest first

    Listing never marks anything as read.

    Args:
        folder: Folder to list. Accepts system names ("inbox", "sent", "archive",
            "spam", "trash", "all mail"), your own folders and labels by their
            plain name ("Work"), or the full Bridge path ("Folders/Work")
            (default: "inbox")
        count: Maximum number of emails to return (default: 10, maximum: 500)
        received_after: Only include emails on or after this date. Accepts
            YYYY-MM-DD, an ISO-8601 timestamp, or relative shorthand such as
            "90d", "6m", "1y" (default: no lower bound)
        received_before: Only include emails on or before this date, same
            formats as received_after (default: no upper bound)
        unread_only: Only return unread emails (default: False)
        include_recipients: Include To/CC addresses in each summary (default: True)

    Returns:
        Formatted list of emails with sender, recipients, subject, date, read
        status, and the message ID to pass to read_email
    """
    count = max(1, min(count, MAX_RESULT_COUNT))

    try:
        return await asyncio.to_thread(
            _list_emails,
            folder,
            count,
            received_after,
            received_before,
            unread_only,
            include_recipients,
        )
    except DateParseError as e:
        return f"Invalid date range: {e}"
    except FolderNotFound as e:
        return str(e)
    except BridgeError as e:
        return str(e)
    except Exception as e:
        return f"Error listing emails: {e}"


def _list_folders(include_counts: bool) -> str:
    with imap(None) as box:
        names = [f.name for f in box.folder.list()]
        counts = {}
        if include_counts:
            for name in names:
                try:
                    counts[name] = box.folder.status(name)
                except Exception:
                    # A folder that can't be STATUSed (Bridge occasionally
                    # refuses \Noselect entries) shouldn't sink the whole list.
                    continue
    return describe_folders(names, counts)


@mcp.tool(name="list_folders", title="List folders")
async def handle_list_folders(include_counts: bool = True) -> str:
    """
    List the folders and labels available in the Proton mailbox

    Proton distinguishes folders (a message lives in exactly one) from labels
    (a message can carry several); Bridge exposes them as "Folders/<name>" and
    "Labels/<name>" alongside the system folders.

    Args:
        include_counts: Include message and unread counts per folder. Costs one
            extra round trip per folder (default: True)

    Returns:
        The mailbox's folders and labels, grouped by kind
    """
    try:
        return await asyncio.to_thread(_list_folders, include_counts)
    except BridgeError as e:
        return str(e)
    except Exception as e:
        return f"Error listing folders: {e}"
