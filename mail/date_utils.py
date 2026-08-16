"""
Date parsing and IMAP SEARCH date-criteria construction.

Callers are allowed to be loose: a plain date, a full ISO-8601 timestamp, or
relative shorthand like ``90d`` / ``6m`` / ``1y``. IMAP itself is much stricter
and much coarser -- SINCE/BEFORE compare whole dates only, with no time
component -- so the parsed instants are floored to a day before being handed to
the server, and the exact-time filtering is finished off client-side.
"""

__all__ = [
    "DateParseError",
    "parse_datetime",
    "build_date_criteria",
    "within_window",
    "as_utc",
]

import re
from datetime import datetime, timedelta, timezone
from typing import Optional

_RELATIVE_RE = re.compile(r"^(\d+)\s*([dwmy])$", re.IGNORECASE)
_RELATIVE_DAYS = {"d": 1, "w": 7, "m": 30, "y": 365}


class DateParseError(ValueError):
    """Raised when a caller-supplied date string can't be interpreted."""


def as_utc(when: Optional[datetime]) -> Optional[datetime]:
    """Normalise a message date to an aware UTC datetime.

    Date headers are wildly inconsistent -- imap_tools hands back naive
    datetimes for messages with a missing or ``-0000`` timezone, and comparing
    those against aware ones raises. Naive values are read as UTC.
    """
    if when is None:
        return None
    if when.tzinfo is None:
        return when.replace(tzinfo=timezone.utc)
    return when.astimezone(timezone.utc)


def parse_datetime(value: str, *, end_of_day: bool = False) -> datetime:
    """Parse a caller-supplied date into a timezone-aware UTC datetime.

    Accepts ``YYYY-MM-DD``, any ISO-8601 timestamp (with ``Z`` or an offset),
    and relative shorthand such as ``30d``, ``6m``, ``1y`` meaning "that long
    ago". ``end_of_day`` pushes a bare date to 23:59:59 so a caller passing the
    same day as both bounds gets that whole day.
    """
    if not value:
        raise DateParseError("Empty date value")

    raw = value.strip()

    relative = _RELATIVE_RE.match(raw)
    if relative:
        amount = int(relative.group(1))
        unit = relative.group(2).lower()
        return datetime.now(timezone.utc) - timedelta(days=amount * _RELATIVE_DAYS[unit])

    iso = raw.replace("Z", "+00:00") if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(iso)
    except ValueError as exc:
        raise DateParseError(
            f"Could not parse date {value!r}. Use YYYY-MM-DD, an ISO-8601 "
            f"timestamp, or relative shorthand like '90d'."
        ) from exc

    # A bare date parses to midnight; only then does end_of_day apply.
    is_bare_date = len(raw) == 10 and parsed.time() == datetime.min.time()
    if end_of_day and is_bare_date:
        parsed = parsed.replace(hour=23, minute=59, second=59)

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def build_date_criteria(
    after: str = "", before: str = "", *, sent_like: bool = False
) -> dict:
    """Build the date keyword arguments for an imap_tools ``AND(...)`` query.

    Returns kwargs for ``date_gte``/``date_lt`` (arrival time, the SINCE/BEFORE
    keys) or ``sent_date_gte``/``sent_date_lt`` (the Date: header, SENTSINCE/
    SENTBEFORE) when the folder holds outbound mail and arrival time is
    meaningless.

    Both bounds are widened to whole days, because that is all IMAP can express:
    ``before`` becomes "strictly before the day after", so the caller's end date
    is included. ``within_window`` then trims the edges precisely.
    """
    prefix = "sent_date" if sent_like else "date"
    criteria = {}

    if after:
        criteria[f"{prefix}_gte"] = parse_datetime(after).date()
    if before:
        # date_lt is exclusive, so add a day to keep `before` itself in range.
        criteria[f"{prefix}_lt"] = (
            parse_datetime(before, end_of_day=True).date() + timedelta(days=1)
        )
    return criteria


def within_window(
    when: Optional[datetime], after: str = "", before: str = ""
) -> bool:
    """Client-side check that a message really falls inside the window.

    The server has already filtered to whole days; this discards the messages on
    the boundary days that fall outside the requested times. Messages with no
    parseable date are kept -- better a spurious result than a silent drop.
    """
    if when is None or (not after and not before):
        return True

    when = as_utc(when)

    if after and when < parse_datetime(after):
        return False
    if before and when > parse_datetime(before, end_of_day=True):
        return False
    return True
