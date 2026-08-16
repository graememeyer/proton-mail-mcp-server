"""
search_emails tool.

Unlike Graph's ranked full-text search, IMAP SEARCH is a straightforward
substring match evaluated by the server and ANDed together, returning results in
mailbox order rather than by relevance. That makes it predictable but literal:
"query" matches a substring of the raw message text, not a set of stemmed terms.
"""

__all__ = ["handle_search_emails", "build_search_criteria"]

import asyncio

from config import DEFAULT_PAGE_SIZE, MAX_RESULT_COUNT
from server import mcp
from .client import BridgeError, imap
from .date_utils import DateParseError, build_date_criteria
from .folders import FolderNotFound, is_sent_like
from .formatting import format_summary
from .query import fetch_summaries


def build_search_criteria(
    query: str = "",
    from_addr: str = "",
    to: str = "",
    subject: str = "",
    body: str = "",
) -> dict:
    """Map the tool's arguments onto imap_tools AND(...) keywords.

    Empty terms are dropped rather than passed as empty strings, which IMAP
    would treat as "matches everything containing the empty string" -- harmless
    but wasteful, and it defeats the server-side filtering.
    """
    criteria = {}
    if query:
        criteria["text"] = query      # TEXT: headers and body
    if body:
        criteria["body"] = body       # BODY: body only
    if subject:
        criteria["subject"] = subject
    if from_addr:
        criteria["from_"] = from_addr
    if to:
        criteria["to"] = to
    return criteria


def _search_emails(
    folder: str,
    count: int,
    criteria: dict,
    received_after: str,
    received_before: str,
    unread_only: bool,
    has_attachments: bool,
    include_recipients: bool,
) -> str:
    """Blocking body of search_emails; runs in a worker thread."""
    criteria = dict(criteria)
    criteria.update(
        build_date_criteria(
            received_after, received_before, sent_like=is_sent_like(folder)
        )
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
            has_attachments=has_attachments,
        )

    if not messages:
        return f"No emails in {resolved} matched your search criteria."

    body_text = "".join(
        format_summary(m, i, resolved, include_recipients=include_recipients)
        for i, m in enumerate(messages, 1)
    )
    note = ""
    if has_attachments:
        note = (
            "\nNote: attachment filtering is done from message structure after "
            "fetching, since IMAP cannot search on it.\n"
        )
    return f"Found {len(messages)} emails in {resolved}:{note}\n{body_text}"


@mcp.tool(name="search_emails", title="Search emails")
async def handle_search_emails(
    folder: str = "inbox",
    count: int = DEFAULT_PAGE_SIZE,
    query: str = "",
    from_addr: str = "",
    to: str = "",
    subject: str = "",
    body: str = "",
    has_attachments: bool = False,
    unread_only: bool = False,
    received_after: str = "",
    received_before: str = "",
    include_recipients: bool = True,
) -> str:
    """
    Search a Proton Mail folder by sender, recipient, subject, text or date

    All supplied criteria are ANDed. Matching is literal substring matching, not
    ranked relevance search, and it is case-insensitive. To search the whole
    mailbox rather than one folder, pass folder="all mail". Searching never
    marks anything as read.

    Args:
        folder: Folder to search; use "all mail" for the whole mailbox
            (default: "inbox")
        count: Maximum number of emails to return (default: 10, maximum: 500)
        query: Substring to find anywhere in the message, headers included
        from_addr: Substring of the sender address or display name
        to: Substring of a recipient address or display name
        subject: Substring of the subject line
        body: Substring to find in the message body only (unlike query, which
            also matches headers)
        has_attachments: Only return emails carrying attachments. Applied after
            fetching, as IMAP cannot search on it (default: False)
        unread_only: Only return unread emails (default: False)
        received_after: Only include emails on or after this date. Accepts
            YYYY-MM-DD, an ISO-8601 timestamp, or relative shorthand like "90d"
        received_before: Only include emails on or before this date
        include_recipients: Include To/CC addresses in each summary (default: True)

    Returns:
        Formatted list of matching emails with sender, recipients, subject,
        date, read status, and the message ID to pass to read_email
    """
    count = max(1, min(count, MAX_RESULT_COUNT))
    criteria = build_search_criteria(query, from_addr, to, subject, body)

    try:
        return await asyncio.to_thread(
            _search_emails,
            folder,
            count,
            criteria,
            received_after,
            received_before,
            unread_only,
            has_attachments,
            include_recipients,
        )
    except DateParseError as e:
        return f"Invalid date range: {e}"
    except FolderNotFound as e:
        return str(e)
    except BridgeError as e:
        return str(e)
    except Exception as e:
        return f"Error searching emails: {e}"
