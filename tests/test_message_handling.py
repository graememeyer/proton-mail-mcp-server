"""
Offline tests for body flattening, outgoing message assembly and search-criteria
construction. These import the application modules (and so need the placeholder
credentials from conftest), but never open a connection.
"""

import pytest

from mail.formatting import html_to_text
from mail.search import build_search_criteria
from mail.send import build_message, looks_like_html, split_addresses


# ------------------------------------------------------------- html_to_text

def test_html_to_text_strips_tags_and_unescapes():
    assert html_to_text("<p>Hello &amp; welcome</p>") == "Hello & welcome"


def test_html_to_text_drops_script_and_style():
    markup = "<style>p{color:red}</style><script>alert(1)</script><p>Real text</p>"
    out = html_to_text(markup)
    assert "Real text" in out
    assert "color:red" not in out and "alert" not in out


def test_block_tags_become_line_breaks():
    out = html_to_text("<p>One</p><p>Two</p>")
    assert out.splitlines() == ["One", "Two"]


def test_br_becomes_newline():
    assert html_to_text("a<br>b") == "a\nb"


def test_runs_of_blank_lines_collapse():
    assert "\n\n\n" not in html_to_text("<p>a</p><br><br><br><br><p>b</p>")


def test_empty_html_is_empty():
    assert html_to_text("") == ""


# ------------------------------------------------------------------- sending

def test_split_addresses_trims_and_drops_blanks():
    assert split_addresses(" a@x.com , b@y.com ,, ") == ["a@x.com", "b@y.com"]
    assert split_addresses("") == []


@pytest.mark.parametrize(
    "body,expected",
    [
        ("<html><body>hi</body></html>", True),
        ("<div>hi</div>", True),
        ("plain text with a < sign", False),
        ("", False),
    ],
)
def test_html_detection(body, expected):
    assert looks_like_html(body) is expected


def test_build_message_sets_headers():
    msg = build_message(
        "me@proton.me", ["a@x.com"], ["c@x.com"], ["b@x.com"], "Hi", "Body"
    )
    assert msg["From"] == "me@proton.me"
    assert msg["To"] == "a@x.com"
    assert msg["Cc"] == "c@x.com"
    assert msg["Subject"] == "Hi"
    assert msg["Message-ID"].endswith("@proton.me>")


def test_bcc_never_appears_in_headers():
    # Bcc is an envelope-only concern; a Bcc header would leak the recipient.
    msg = build_message("me@proton.me", ["a@x.com"], [], ["secret@x.com"], "Hi", "Body")
    assert msg["Bcc"] is None
    assert "secret@x.com" not in msg.as_string()


def test_normal_importance_adds_no_headers():
    msg = build_message("me@proton.me", ["a@x.com"], [], [], "Hi", "Body")
    assert msg["X-Priority"] is None and msg["Importance"] is None


def test_high_importance_headers():
    msg = build_message(
        "me@proton.me", ["a@x.com"], [], [], "Hi", "Body", importance="high"
    )
    assert msg["X-Priority"] == "1" and msg["Importance"] == "High"


def test_plain_body_is_single_part_text():
    msg = build_message("me@proton.me", ["a@x.com"], [], [], "Hi", "Just text")
    assert msg.get_content_type() == "text/plain"
    assert "Just text" in msg.get_content()


def test_html_body_keeps_a_plain_text_alternative():
    msg = build_message(
        "me@proton.me", ["a@x.com"], [], [], "Hi", "<html><b>Bold</b></html>"
    )
    assert msg.get_content_type() == "multipart/alternative"
    subtypes = [p.get_content_type() for p in msg.iter_parts()]
    assert subtypes == ["text/plain", "text/html"]


def test_threading_headers_chain_the_conversation():
    msg = build_message(
        "me@proton.me", ["a@x.com"], [], [], "Re: Hi", "Body",
        thread=("<orig@x.com>", "<older@x.com>"),
    )
    assert msg["In-Reply-To"] == "<orig@x.com>"
    # References is the prior chain plus the message being replied to.
    assert msg["References"] == "<older@x.com> <orig@x.com>"


def test_threading_without_prior_references():
    msg = build_message(
        "me@proton.me", ["a@x.com"], [], [], "Re: Hi", "Body",
        thread=("<orig@x.com>", ""),
    )
    assert msg["References"] == "<orig@x.com>"


def test_no_thread_means_no_threading_headers():
    msg = build_message("me@proton.me", ["a@x.com"], [], [], "Hi", "Body")
    assert msg["In-Reply-To"] is None and msg["References"] is None


# ------------------------------------------------------------ search criteria

def test_search_criteria_maps_arguments():
    assert build_search_criteria(
        query="q", from_addr="a@x.com", to="b@x.com", subject="s", body="b"
    ) == {
        "text": "q",
        "body": "b",
        "subject": "s",
        "from_": "a@x.com",
        "to": "b@x.com",
    }


def test_empty_terms_are_dropped():
    # An empty term would match everything and defeat server-side filtering.
    assert build_search_criteria(query="", from_addr="a@x.com") == {"from_": "a@x.com"}


def test_no_terms_is_empty():
    assert build_search_criteria() == {}
