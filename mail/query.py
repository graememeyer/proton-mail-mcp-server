"""
The shared fetch path behind list_emails and search_emails.

IMAP does not page: SEARCH returns every matching UID at once and the client
decides what to FETCH. imap_tools' ``reverse=True`` walks that UID set from the
end, so asking for 10 messages fetches 10 messages regardless of how many
matched -- the cost scales with the count requested, not the size of the folder.

Some criteria have no IMAP equivalent (attachments) and some are coarser on the
server than in the tool signature (dates are whole-day only). Those are finished
off client-side, which is why the fetch over-reads before slicing.
"""

__all__ = ["fetch_summaries"]

from typing import List, Optional

from imap_tools import AND

from config import MAX_RESULT_COUNT
from .date_utils import as_utc, within_window
from .formatting import has_attachments_hint

# When any filter has to run client-side, over-read so that discarded messages
# don't leave the caller short of the count they asked for.
_OVERFETCH = 3
_OVERFETCH_FLOOR = 20


def _charset_for(criteria: dict) -> str:
    """Pick the SEARCH charset for the given criteria.

    US-ASCII is the safe default that every server accepts; UTF-8 is only
    declared when a search term actually needs it, since some servers reject a
    UTF-8 SEARCH they don't have to handle.
    """
    for value in criteria.values():
        if isinstance(value, str) and not value.isascii():
            return "UTF-8"
    return "US-ASCII"


def fetch_summaries(
    box,
    folder: str,
    count: int,
    *,
    criteria: Optional[dict] = None,
    received_after: str = "",
    received_before: str = "",
    unread_only: bool = False,
    has_attachments: bool = False,
) -> List:
    """Run a search and return up to ``count`` messages, newest first.

    ``criteria`` is the imap_tools AND(...) keyword set built by the caller
    (addresses, subject, text, dates). ``received_after``/``received_before``
    are passed again in their raw form so the day-granular server filter can be
    tightened to the exact instants requested.
    """
    count = max(1, min(count, MAX_RESULT_COUNT))
    search = dict(criteria or {})
    if unread_only:
        search["seen"] = False

    # Anything the server can't express is applied after the fetch, so read more
    # than asked for when such a filter is in play.
    client_side = bool(has_attachments or received_after or received_before)
    limit = count if not client_side else max(count * _OVERFETCH, _OVERFETCH_FLOOR)
    limit = min(limit, MAX_RESULT_COUNT * _OVERFETCH)

    messages = list(
        box.fetch(
            AND(**search) if search else "ALL",
            charset=_charset_for(search),
            limit=limit,
            reverse=True,        # newest UIDs first, so `limit` keeps the recent end
            mark_seen=False,     # listing mail must never mark it read
            headers_only=True,   # summaries need no body; bodies are large
            bulk=True,           # one FETCH round trip for the whole batch
        )
    )

    if has_attachments:
        messages = [m for m in messages if has_attachments_hint(m)]
    if received_after or received_before:
        messages = [
            m for m in messages
            if within_window(m.date, received_after, received_before)
        ]

    # UID order tracks arrival order on Bridge, but a re-imported or moved
    # message can sit out of sequence, so sort on the header date for display.
    # as_utc keeps naive and aware dates comparable in the same sort.
    messages.sort(key=lambda m: as_utc(m.date), reverse=True)
    return messages[:count]
