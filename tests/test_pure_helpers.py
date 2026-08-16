"""
Offline tests for the pure helpers: date parsing, IMAP criteria construction,
folder resolution, message ids, HTML flattening and message assembly.

These deliberately avoid importing anything that opens a connection, so they run
without Proton Bridge, credentials or network access.
"""

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mail.date_utils import (  # noqa: E402
    DateParseError,
    as_utc,
    build_date_criteria,
    parse_datetime,
    within_window,
)
from mail.folders import (  # noqa: E402
    FolderNotFound,
    describe_folders,
    is_sent_like,
    pick_folder,
)
from mail.ids import BadMessageId, decode_id, encode_id  # noqa: E402


# ---------------------------------------------------------------- date_utils

def test_parse_bare_date_is_midnight_utc():
    assert parse_datetime("2026-01-01") == datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_parse_bare_date_end_of_day():
    dt = parse_datetime("2026-01-01", end_of_day=True)
    assert (dt.hour, dt.minute, dt.second) == (23, 59, 59)


def test_end_of_day_does_not_touch_explicit_timestamps():
    dt = parse_datetime("2026-01-01T09:30:00Z", end_of_day=True)
    assert (dt.hour, dt.minute) == (9, 30)


def test_parse_iso_with_offset_normalises_to_utc():
    dt = parse_datetime("2026-03-01T12:00:00+02:00")
    assert dt == datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "shorthand,approx_days", [("7d", 7), ("2w", 14), ("6m", 180), ("1y", 365)]
)
def test_relative_shorthand(shorthand, approx_days):
    delta = datetime.now(timezone.utc) - parse_datetime(shorthand)
    assert abs(delta.days - approx_days) <= 1


def test_bad_date_raises():
    with pytest.raises(DateParseError):
        parse_datetime("last Tuesday-ish")


def test_as_utc_promotes_naive_dates():
    # imap_tools returns naive datetimes for headers with no zone or "-0000";
    # they must stay comparable with aware ones.
    assert as_utc(datetime(2026, 1, 1, 12)) == datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    assert as_utc(None) is None
    assert as_utc(datetime(2026, 1, 1, 12, tzinfo=timezone(timedelta(hours=2)))) == (
        datetime(2026, 1, 1, 10, tzinfo=timezone.utc)
    )


def test_date_criteria_uses_arrival_keys_by_default():
    criteria = build_date_criteria("2026-01-01", "2026-08-12")
    assert criteria == {
        "date_gte": date(2026, 1, 1),
        # date_lt is exclusive, so the end date is pushed a day out to include it
        "date_lt": date(2026, 8, 13),
    }


def test_date_criteria_uses_sent_keys_for_outbound_folders():
    criteria = build_date_criteria("2026-01-01", sent_like=True)
    assert criteria == {"sent_date_gte": date(2026, 1, 1)}


def test_date_criteria_empty_when_no_bounds():
    assert build_date_criteria("", "") == {}


def test_within_window_trims_the_boundary_day():
    # IMAP filters to whole days, so a message earlier on the start date comes
    # back from the server and has to be discarded client-side.
    when = datetime(2026, 1, 1, 6, 0, tzinfo=timezone.utc)
    assert not within_window(when, after="2026-01-01T09:00:00Z")
    assert within_window(when, after="2026-01-01T05:00:00Z")


def test_within_window_includes_whole_end_day():
    when = datetime(2026, 8, 12, 23, 30, tzinfo=timezone.utc)
    assert within_window(when, before="2026-08-12")


def test_within_window_keeps_undated_messages():
    assert within_window(None, after="2026-01-01")


def test_within_window_is_a_noop_without_bounds():
    assert within_window(datetime(1990, 1, 1, tzinfo=timezone.utc))


# ------------------------------------------------------------------- folders

AVAILABLE = [
    "INBOX",
    "Sent",
    "Drafts",
    "Archive",
    "Spam",
    "Trash",
    "All Mail",
    "Folders/Work",
    "Folders/Receipts",
    "Labels/Important",
    "Labels/Work",
]


@pytest.mark.parametrize(
    "requested,expected",
    [
        ("INBOX", "INBOX"),
        ("inbox", "INBOX"),
        ("junk", "Spam"),
        ("bin", "Trash"),
        ("deleted items", "Trash"),
        ("sentitems", "Sent"),
        ("all mail", "All Mail"),
        ("allmail", "All Mail"),
        ("all", "All Mail"),
        ("Folders/Work", "Folders/Work"),
        ("folders/work", "Folders/Work"),
        ("Receipts", "Folders/Receipts"),
        ("important", "Labels/Important"),
    ],
)
def test_pick_folder_resolves_aliases_and_leaves(requested, expected):
    assert pick_folder(requested, AVAILABLE) == expected


def test_leaf_match_prefers_folders_over_labels():
    # "Work" exists as both a folder and a label; the exclusive folder wins.
    assert pick_folder("Work", AVAILABLE) == "Folders/Work"


def test_exact_match_beats_alias():
    # A user folder literally named "Spam" must not be hijacked by the alias.
    assert pick_folder("Folders/Spam", AVAILABLE + ["Folders/Spam"]) == "Folders/Spam"


def test_empty_folder_defaults_to_inbox():
    assert pick_folder("", AVAILABLE) == "INBOX"


def test_unknown_folder_lists_the_alternatives():
    with pytest.raises(FolderNotFound) as exc:
        pick_folder("Nonsense", AVAILABLE)
    assert "Folders/Work" in str(exc.value)


def test_is_sent_like():
    assert is_sent_like("Sent")
    assert is_sent_like("drafts")
    assert not is_sent_like("INBOX")
    assert not is_sent_like("Folders/Work")


def test_describe_folders_groups_by_kind():
    out = describe_folders(AVAILABLE, {"INBOX": {"MESSAGES": 12, "UNSEEN": 3}})
    assert "System folders:" in out
    assert "Your folders" in out and "Your labels" in out
    assert "INBOX (12 messages, 3 unread)" in out
    # Grouping must be by prefix, not alphabetical position.
    assert out.index("INBOX") < out.index("Folders/Work") < out.index("Labels/Important")


def test_describe_folders_handles_empty():
    assert describe_folders([]) == "No folders found."


# ----------------------------------------------------------------------- ids

def test_id_round_trips():
    assert decode_id(encode_id("INBOX", "1234")) == ("INBOX", "1234")


def test_id_round_trips_for_namespaced_folders():
    # The folder name contains a slash and could contain a colon; splitting on
    # the LAST colon is what keeps this working.
    assert decode_id(encode_id("Folders/Work", "77")) == ("Folders/Work", "77")


def test_id_with_colon_in_folder_name():
    assert decode_id("Folders/A:B:99") == ("Folders/A:B", "99")


@pytest.mark.parametrize("bad", ["", "   ", "INBOX", "1234", ":1234", "INBOX:", "INBOX:abc"])
def test_bad_ids_rejected(bad):
    with pytest.raises(BadMessageId):
        decode_id(bad)
