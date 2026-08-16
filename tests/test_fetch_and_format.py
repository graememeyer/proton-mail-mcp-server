"""
Tests for the fetch pipeline and message rendering, driven by real imap_tools
MailMessage objects built from raw RFC822 bytes.

The messages are genuine (parsed by imap_tools exactly as they would be off the
wire); only the mailbox is a stand-in, so the client-side filtering, sorting and
formatting are exercised for real without needing Proton Bridge.
"""

import pytest

from mail.formatting import format_detail, format_summary, has_attachments_hint
from mail.query import fetch_summaries


def make_message(
    uid: str,
    *,
    subject: str = "Subject",
    sender: str = "Alice <alice@example.com>",
    to: str = "Bob <bob@example.com>",
    cc: str = "",
    date: str = "Sat, 15 Aug 2026 10:00:00 +0100",
    seen: bool = True,
    content_type: str = "text/plain",
    body: str = "Body here",
):
    """Build a MailMessage the way imap_tools builds one from a FETCH response."""
    from imap_tools import MailMessage

    headers = [
        f"From: {sender}",
        f"To: {to}",
        f"Subject: {subject}",
        f"Content-Type: {content_type}",
        f"Message-ID: <{uid}@example.com>",
    ]
    if cc:
        headers.append(f"Cc: {cc}")
    if date:
        headers.append(f"Date: {date}")

    raw = ("\r\n".join(headers) + "\r\n\r\n" + body + "\r\n").encode()
    flags = "\\Seen" if seen else ""
    prefix = f"1 (UID {uid} RFC822.SIZE {len(raw)} FLAGS ({flags}) BODY[] ".encode()
    prefix += b"{%d}" % len(raw)
    return MailMessage([(prefix, raw), b")"])


class FakeBox:
    """Minimal stand-in for an imap_tools MailBox, recording how it was called."""

    def __init__(self, messages):
        self._messages = messages
        self.calls = []

    def fetch(self, criteria="ALL", charset="US-ASCII", **kwargs):
        self.calls.append({"criteria": criteria, "charset": charset, **kwargs})
        messages = self._messages
        if kwargs.get("reverse"):
            messages = list(reversed(messages))
        limit = kwargs.get("limit")
        return iter(messages[:limit] if limit else messages)


# ------------------------------------------------------------ fetch_summaries

def test_listing_never_marks_mail_read():
    # The single most important property of the list path: BODY.PEEK, not BODY.
    box = FakeBox([make_message("1")])
    fetch_summaries(box, "INBOX", 10)
    assert box.calls[0]["mark_seen"] is False


def test_summaries_are_headers_only():
    box = FakeBox([make_message("1")])
    fetch_summaries(box, "INBOX", 10)
    assert box.calls[0]["headers_only"] is True


def test_newest_first_across_mixed_timezones():
    # A missing or "-0000" zone yields a naive datetime; sorting those beside
    # aware ones raises TypeError unless they are normalised first.
    messages = [
        make_message("1", subject="oldest", date="Sat, 15 Aug 2026 08:00:00 +0000"),
        make_message("2", subject="naive", date="Sat, 15 Aug 2026 12:00:00 -0000"),
        make_message("3", subject="newest", date="Sat, 15 Aug 2026 15:00:00 +0100"),
    ]
    out = fetch_summaries(FakeBox(messages), "INBOX", 10)
    assert [m.subject for m in out] == ["newest", "naive", "oldest"]


def test_undated_messages_sort_last_without_raising():
    messages = [
        make_message("1", subject="dated", date="Sat, 15 Aug 2026 08:00:00 +0000"),
        make_message("2", subject="undated", date=""),
    ]
    out = fetch_summaries(FakeBox(messages), "INBOX", 10)
    # An absent Date header parses to 1900-01-01, so it sorts to the bottom.
    assert [m.subject for m in out] == ["dated", "undated"]


def test_count_is_respected():
    messages = [make_message(str(i)) for i in range(1, 6)]
    assert len(fetch_summaries(FakeBox(messages), "INBOX", 2)) == 2


def test_unread_only_becomes_a_server_side_criterion():
    box = FakeBox([make_message("1", seen=False)])
    fetch_summaries(box, "INBOX", 10, unread_only=True)
    # imap_tools renders AND(seen=False) as UNSEEN; check the query object built.
    assert "UNSEEN" in str(box.calls[0]["criteria"])


def test_attachment_filter_runs_client_side():
    messages = [
        make_message("1", subject="plain", content_type="text/plain"),
        make_message("2", subject="attached", content_type='multipart/mixed; boundary="b"'),
        make_message("3", subject="inline", content_type='multipart/related; boundary="b"'),
    ]
    out = fetch_summaries(FakeBox(messages), "INBOX", 10, has_attachments=True)
    assert [m.subject for m in out] == ["attached"]


def test_client_side_filters_trigger_overfetch():
    # The server can't filter attachments, so more must be read than requested
    # or the caller ends up short.
    box = FakeBox([make_message(str(i)) for i in range(1, 60)])
    fetch_summaries(box, "INBOX", 5, has_attachments=True)
    assert box.calls[0]["limit"] > 5


def test_no_overfetch_when_everything_is_server_side():
    box = FakeBox([make_message(str(i)) for i in range(1, 60)])
    fetch_summaries(box, "INBOX", 5)
    assert box.calls[0]["limit"] == 5


def test_date_window_trims_the_boundary_day():
    # Both survive the server's whole-day SINCE filter; only one is inside the
    # requested time window.
    messages = [
        make_message("1", subject="early", date="Sat, 15 Aug 2026 06:00:00 +0000"),
        make_message("2", subject="late", date="Sat, 15 Aug 2026 18:00:00 +0000"),
    ]
    out = fetch_summaries(
        FakeBox(messages), "INBOX", 10, received_after="2026-08-15T12:00:00Z"
    )
    assert [m.subject for m in out] == ["late"]


def test_utf8_charset_only_when_needed():
    box = FakeBox([make_message("1")])
    fetch_summaries(box, "INBOX", 10, criteria={"subject": "hello"})
    assert box.calls[0]["charset"] == "US-ASCII"

    box = FakeBox([make_message("1")])
    fetch_summaries(box, "INBOX", 10, criteria={"subject": "café"})
    assert box.calls[0]["charset"] == "UTF-8"


# ---------------------------------------------------------------- formatting

def test_summary_contains_the_reusable_id():
    msg = make_message("42")
    out = format_summary(msg, 1, "Folders/Work")
    assert "ID: Folders/Work:42" in out


def test_unread_marker():
    assert "[UNREAD]" in format_summary(make_message("1", seen=False), 1, "INBOX")
    assert "[UNREAD]" not in format_summary(make_message("1", seen=True), 1, "INBOX")


def test_attachment_marker_from_headers_only_fetch():
    msg = make_message("1", content_type='multipart/mixed; boundary="b"')
    assert "[ATTACHMENTS]" in format_summary(msg, 1, "INBOX")


def test_summary_dates_are_normalised_to_utc():
    msg = make_message("1", date="Sat, 15 Aug 2026 10:00:00 +0100")
    assert "2026-08-15T09:00:00Z" in format_summary(msg, 1, "INBOX")


def test_recipients_can_be_omitted():
    msg = make_message("1")
    assert "To:" in format_summary(msg, 1, "INBOX", include_recipients=True)
    assert "To:" not in format_summary(msg, 1, "INBOX", include_recipients=False)


def test_cc_shown_only_when_present():
    assert "CC:" not in format_summary(make_message("1"), 1, "INBOX")
    with_cc = make_message("1", cc="Carol <carol@example.com>")
    assert "CC: Carol (carol@example.com)" in format_summary(with_cc, 1, "INBOX")


def test_large_recipient_lists_are_collapsed():
    many = ", ".join(f"p{i} <p{i}@example.com>" for i in range(10))
    out = format_summary(make_message("1", to=many), 1, "INBOX")
    assert "(+6 more)" in out


def test_detail_includes_body_and_metadata():
    msg = make_message("7", subject="Quarterly", body="The numbers are in.")
    out = format_detail(msg, "INBOX")
    assert "Subject: Quarterly" in out
    assert "The numbers are in." in out
    assert "ID: INBOX:7" in out
    assert "Read: Yes" in out


def test_detail_reports_unread_state():
    assert "Read: No" in format_detail(make_message("1", seen=False), "INBOX")


def test_detail_flattens_html_bodies():
    msg = make_message(
        "1", content_type="text/html", body="<p>Hello <b>there</b></p>"
    )
    out = format_detail(msg, "INBOX")
    assert "Hello there" in out
    assert "<p>" not in out


def test_missing_body_is_reported():
    msg = make_message("1", body="")
    assert "(no body content)" in format_detail(msg, "INBOX")


def test_body_is_truncated_when_enormous():
    from config import MAX_BODY_CHARS

    msg = make_message("1", body="x" * (MAX_BODY_CHARS + 500))
    out = format_detail(msg, "INBOX")
    assert "truncated" in out
    assert len(out) < MAX_BODY_CHARS + 1000


@pytest.mark.parametrize(
    "content_type,expected",
    [
        ("text/plain", False),
        ('multipart/mixed; boundary="b"', True),
        ('multipart/alternative; boundary="b"', False),
        ('multipart/related; boundary="b"', False),
    ],
)
def test_attachment_hint_by_content_type(content_type, expected):
    assert has_attachments_hint(make_message("1", content_type=content_type)) is expected
