"""
End-to-end tests for SMTP submission, run against a loopback SMTP sink that
stands in for Proton Bridge.

These exist because the From override splits one value in two: the header, which
may carry a display name, and the envelope sender, which may not -- Bridge
matches the envelope against the addresses on the account. Asserting on
build_message alone cannot catch the two being crossed, so these drive the real
smtplib path in client.smtp_connection and read what arrived on the wire.

Still offline: loopback only, no Bridge, no credentials, no network.
"""

import base64
import socket
import socketserver
import threading

import pytest

from config import settings
from mail.send import _send_email


class _Transcript:
    """What the sink saw: the envelope, the recipients and the DATA blob."""

    def __init__(self):
        self.mail_from = None
        self.rcpt_tos = []
        self.data = ""
        self.auth = None


class _SinkHandler(socketserver.StreamRequestHandler):
    """A minimal ESMTP server: enough verbs for smtplib to complete a send."""

    def handle(self):
        transcript = self.server.transcript
        self.wfile.write(b"220 sink ESMTP\r\n")

        while True:
            line = self.rfile.readline()
            if not line:
                return
            command = line.decode("utf-8", "replace").strip()
            upper = command.upper()

            if upper.startswith("EHLO"):
                # AUTH is advertised because client.smtp_connection always logs
                # in. SIZE/8BITMIME are deliberately not, to keep MAIL FROM bare.
                self.wfile.write(b"250-sink\r\n250 AUTH PLAIN\r\n")
            elif upper.startswith("HELO"):
                self.wfile.write(b"250 sink\r\n")
            elif upper.startswith("AUTH PLAIN"):
                token = command.split(" ", 2)[2] if command.count(" ") >= 2 else ""
                transcript.auth = base64.b64decode(token).decode() if token else ""
                self.wfile.write(b"235 authenticated\r\n")
            elif upper.startswith("MAIL FROM:"):
                transcript.mail_from = command[len("MAIL FROM:"):].strip().strip("<>")
                self.wfile.write(b"250 ok\r\n")
            elif upper.startswith("RCPT TO:"):
                transcript.rcpt_tos.append(
                    command[len("RCPT TO:"):].strip().strip("<>")
                )
                self.wfile.write(b"250 ok\r\n")
            elif upper == "DATA":
                self.wfile.write(b"354 go ahead\r\n")
                lines = []
                while True:
                    chunk = self.rfile.readline()
                    if not chunk or chunk in (b".\r\n", b".\n"):
                        break
                    lines.append(chunk.decode("utf-8", "replace"))
                transcript.data = "".join(lines)
                self.wfile.write(b"250 queued\r\n")
            elif upper == "QUIT":
                self.wfile.write(b"221 bye\r\n")
                return
            elif upper == "RSET":
                self.wfile.write(b"250 ok\r\n")
            else:
                self.wfile.write(b"502 not implemented\r\n")


class _Sink(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


@pytest.fixture
def sink(monkeypatch):
    """Run the sink on a free port and point the settings at it."""
    server = _Sink(("127.0.0.1", 0), _SinkHandler)
    server.transcript = _Transcript()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    monkeypatch.setattr(settings, "PROTON_BRIDGE_HOST", "127.0.0.1")
    monkeypatch.setattr(settings, "PROTON_SMTP_PORT", server.server_address[1])
    monkeypatch.setattr(settings, "PROTON_SMTP_SECURITY", "none")

    try:
        yield server.transcript
    finally:
        server.shutdown()
        server.server_close()


def _send(**kwargs):
    args = {
        "to": "someone@example.com",
        "cc": "",
        "bcc": "",
        "subject": "Subject",
        "body": "Body",
        "importance": "normal",
        "reply_to_id": "",
    }
    args.update(kwargs)
    return _send_email(**args)


def test_default_sender_is_unchanged_when_from_address_is_omitted(sink):
    result = _send()
    assert sink.mail_from == "test@proton.me"
    assert "From: test@proton.me" in sink.data
    assert "From: test@proton.me" in result


def test_from_address_overrides_both_header_and_envelope(sink):
    _send(from_address="other@protonmail.com")
    assert sink.mail_from == "other@protonmail.com"
    assert "From: other@protonmail.com" in sink.data


def test_display_name_sender_keeps_the_envelope_bare(sink):
    # The header may carry the display name; the envelope must not, or Bridge
    # will not recognise it as an address on the account.
    _send(from_address="Graeme <other@protonmail.com>")
    assert sink.mail_from == "other@protonmail.com"
    assert "From: Graeme <other@protonmail.com>" in sink.data


def test_login_still_uses_the_bridge_account_not_the_from_address(sink):
    # Overriding From must not change who we authenticate as: Bridge has one
    # credential pair for the whole account in combined addresses mode.
    _send(from_address="other@protonmail.com")
    assert settings.PROTON_BRIDGE_USER in sink.auth
    assert "other@protonmail.com" not in sink.auth


def test_bcc_reaches_the_envelope_but_not_the_headers(sink):
    _send(from_address="other@protonmail.com", bcc="secret@example.com")
    assert "secret@example.com" in sink.rcpt_tos
    assert "secret@example.com" not in sink.data


def test_invalid_from_address_never_opens_a_connection(sink):
    from mail.send import InvalidSender

    with pytest.raises(InvalidSender):
        _send(from_address="not-an-address")
    assert sink.mail_from is None
