"""
Connection management for Proton Bridge's IMAP and SMTP endpoints.

Bridge is a local daemon: it owns the Proton account credentials, does the SRP
login and all OpenPGP encrypt/decrypt work, and re-exposes the mailbox as an
ordinary IMAP/SMTP server on loopback. Everything in this package therefore
speaks plain IMAP -- there is no Proton-specific protocol code anywhere here.

IMAP is a stateful, single-threaded protocol, so a single connection is kept
open and serialised behind a lock. Callers are expected to run these functions
in a worker thread (``asyncio.to_thread``) since imap_tools is blocking.
"""

__all__ = ["imap", "smtp_connection", "check_connection", "BridgeError"]

import smtplib
import ssl
import threading
from contextlib import contextmanager

from imap_tools import MailBox, MailBoxStartTls, MailBoxUnencrypted

from config import settings
from logger import logger
from .folders import FolderNotFound, pick_folder


class BridgeError(RuntimeError):
    """Raised when Proton Bridge can't be reached or refuses the credentials."""


def _ssl_context() -> ssl.SSLContext:
    """TLS settings for Bridge's self-signed, per-install certificate.

    Bridge mints its own CA at install time, so the certificate chains to
    nothing the system trusts. The socket never leaves loopback, so by default
    the chain and hostname checks are dropped. Setting PROTON_TLS_VERIFY=true
    restores full verification, for deployments that have imported the Bridge
    CA into the system trust store.
    """
    context = ssl.create_default_context()
    if not settings.PROTON_TLS_VERIFY:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    return context


def _new_mailbox():
    """Open and authenticate a fresh IMAP connection to Bridge."""
    security = settings.PROTON_IMAP_SECURITY.lower()
    host, port = settings.PROTON_BRIDGE_HOST, settings.PROTON_IMAP_PORT

    if security == "starttls":
        box = MailBoxStartTls(
            host=host, port=port, timeout=settings.PROTON_TIMEOUT,
            ssl_context=_ssl_context(),
        )
    elif security in ("ssl", "tls"):
        box = MailBox(
            host=host, port=port, timeout=settings.PROTON_TIMEOUT,
            ssl_context=_ssl_context(),
        )
    elif security == "none":
        box = MailBoxUnencrypted(host=host, port=port, timeout=settings.PROTON_TIMEOUT)
    else:
        raise BridgeError(
            f"Unknown PROTON_IMAP_SECURITY {settings.PROTON_IMAP_SECURITY!r}; "
            f"expected 'starttls', 'ssl' or 'none'."
        )

    return box.login(settings.PROTON_BRIDGE_USER, settings.PROTON_BRIDGE_PASSWORD)


class _Connection:
    """A lazily-opened IMAP connection, reopened when it goes stale.

    Bridge drops idle connections and restarts on its own schedule (upgrades,
    account re-auth), so a cached connection can't be assumed live. Each
    checkout probes with NOOP and reconnects once if that fails.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._box = None

    def _healthy(self) -> bool:
        if self._box is None:
            return False
        try:
            self._box.client.noop()
            return True
        except Exception:
            return False

    def _discard(self):
        if self._box is not None:
            try:
                self._box.logout()
            except Exception:
                pass  # already dead; nothing useful to do
            self._box = None

    @contextmanager
    def checkout(self, folder=None):
        """Yield a logged-in mailbox with ``folder`` selected.

        ``folder`` is a caller-supplied name ("inbox", "junk", "Work") resolved
        against the mailbox's real folder list; pass None to skip selection when
        the caller only needs to enumerate folders.

        Serialised: one IMAP command sequence at a time. On any failure the
        connection is dropped rather than returned to the cache, so the next
        caller reconnects instead of inheriting a half-broken session.
        """
        with self._lock:
            if not self._healthy():
                self._discard()
                try:
                    self._box = _new_mailbox()
                except Exception as exc:
                    raise BridgeError(_describe(exc)) from exc

            try:
                if folder is not None:
                    available = [f.name for f in self._box.folder.list()]
                    target = pick_folder(folder, available)  # may raise FolderNotFound
                    self._box.folder.set(target)
            except FolderNotFound:
                # A bad folder name is the caller's mistake, not a sick socket;
                # keep the connection for the next call.
                raise
            except Exception:
                self._discard()
                raise

            try:
                yield self._box
            except Exception:
                self._discard()
                raise


def _describe(exc: Exception) -> str:
    """Turn a connection failure into something actionable in a tool response."""
    text = str(exc)
    host = f"{settings.PROTON_BRIDGE_HOST}:{settings.PROTON_IMAP_PORT}"

    if isinstance(exc, (ConnectionRefusedError, OSError)) and "refused" in text.lower():
        return (
            f"Could not reach Proton Bridge at {host}. Check that the Bridge "
            f"service is running and listening on that port."
        )
    if "AUTHENTICATIONFAILED" in text or "Invalid credentials" in text:
        return (
            "Proton Bridge rejected the credentials. PROTON_BRIDGE_PASSWORD must "
            "be the password Bridge generates for mail clients (see `bridge "
            "--cli` then `info`), not the Proton account password."
        )
    if "certificate" in text.lower():
        return (
            f"TLS handshake with Bridge failed: {text}. Bridge uses a self-signed "
            f"certificate; set PROTON_TLS_VERIFY=false or import the Bridge CA."
        )
    return f"Proton Bridge connection failed ({host}): {text}"


_connection = _Connection()


@contextmanager
def imap(folder="INBOX"):
    """Context manager yielding a logged-in mailbox with ``folder`` selected.

    The folder name is resolved leniently (see folders.pick_folder); pass None
    to connect without selecting anything.
    """
    with _connection.checkout(folder) as box:
        yield box


@contextmanager
def smtp_connection():
    """Yield an authenticated SMTP session against Bridge.

    Unlike IMAP this is not pooled: submission is infrequent, and a fresh
    connection avoids holding an authenticated session open between sends.
    """
    security = settings.PROTON_SMTP_SECURITY.lower()
    host, port = settings.PROTON_BRIDGE_HOST, settings.PROTON_SMTP_PORT

    try:
        if security in ("ssl", "tls"):
            server = smtplib.SMTP_SSL(
                host, port, timeout=settings.PROTON_TIMEOUT, context=_ssl_context()
            )
        else:
            server = smtplib.SMTP(host, port, timeout=settings.PROTON_TIMEOUT)
            if security == "starttls":
                server.ehlo()
                server.starttls(context=_ssl_context())
                server.ehlo()
    except Exception as exc:
        raise BridgeError(
            f"Could not reach Proton Bridge SMTP at {host}:{port}: {exc}"
        ) from exc

    try:
        server.login(settings.PROTON_BRIDGE_USER, settings.PROTON_BRIDGE_PASSWORD)
        yield server
    except smtplib.SMTPAuthenticationError as exc:
        raise BridgeError(
            "Proton Bridge rejected the SMTP credentials. Use the Bridge-generated "
            "mail client password, not the Proton account password."
        ) from exc
    finally:
        try:
            server.quit()
        except Exception:
            pass


def check_connection() -> str:
    """Probe Bridge and describe what came back. Used by check_auth_status."""
    try:
        with imap("INBOX") as box:
            status = box.folder.status("INBOX")
            return (
                f"Connected to Proton Bridge at {settings.PROTON_BRIDGE_HOST}:"
                f"{settings.PROTON_IMAP_PORT} as {settings.PROTON_BRIDGE_USER}. "
                f"INBOX holds {status.get('MESSAGES', '?')} messages "
                f"({status.get('UNSEEN', '?')} unread)."
            )
    except BridgeError as exc:
        return f"Not connected. {exc}"
    except Exception as exc:
        logger.error("Unexpected error probing Bridge: %s", exc)
        return f"Not connected. Unexpected error talking to Proton Bridge: {exc}"
