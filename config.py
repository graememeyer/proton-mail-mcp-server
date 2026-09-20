__all__ = ["settings", "SERVER_NAME", "SERVER_VERSION"]

from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    SERVER_NAME: str = "proton-assistant"
    SERVER_VERSION: str = "v1.0.0"

    # ------------------------------------------------------------ Proton Bridge
    # The server never talks to the Proton API directly. Proton Bridge holds the
    # account credentials, performs SRP login and all OpenPGP work, and exposes
    # the decrypted mailbox over plain IMAP/SMTP on the loopback interface.
    PROTON_BRIDGE_HOST: str = "127.0.0.1"
    PROTON_IMAP_PORT: int = 1143
    PROTON_SMTP_PORT: int = 1025

    # The Bridge username is the Proton address; the password is the one Bridge
    # generates per client (`bridge --cli` -> `info`), NOT the Proton password.
    PROTON_BRIDGE_USER: str
    PROTON_BRIDGE_PASSWORD: str

    # Default address for the From header when sending; send_email's
    # from_address parameter overrides it per message. Defaults to the Bridge
    # user. Any value here must be an active address on the same account.
    PROTON_SEND_FROM: Optional[str] = None

    # Bridge listens with STARTTLS on both ports by default. "ssl" (implicit TLS)
    # and "none" are accepted for unusual Bridge configurations, e.g. a Docker
    # image built with SSL disabled.
    PROTON_IMAP_SECURITY: str = "starttls"
    PROTON_SMTP_SECURITY: str = "starttls"

    # Bridge presents a self-signed certificate from its own per-install CA, so
    # chain verification fails by default. The connection never leaves loopback,
    # so verification is off unless the Bridge CA has been added to the system
    # trust store (in which case set this True).
    PROTON_TLS_VERIFY: bool = False

    # Seconds before an IMAP/SMTP operation is abandoned. Bridge can be slow on
    # first contact while it builds its local message cache.
    PROTON_TIMEOUT: int = 60

    # ------------------------------------------------------------------ Transport
    # "stdio" (default) for local use with Claude Code/desktop; "http"
    # (streamable HTTP) for the hosted deployment behind authentik forward-auth.
    MCP_TRANSPORT: str = "stdio"
    MCP_HOST: str = "127.0.0.1"
    MCP_PORT: int = 8000
    MCP_PATH: str = "/mcp"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @property
    def send_from(self) -> str:
        return self.PROTON_SEND_FROM or self.PROTON_BRIDGE_USER


settings = Settings()

SERVER_NAME = settings.SERVER_NAME
SERVER_VERSION = settings.SERVER_VERSION

# Maximum messages a single list/search call will return. IMAP has no server-side
# paging: the whole matching UID set comes back from SEARCH and is then sliced,
# so this bounds the FETCH, not the SEARCH.
MAX_RESULT_COUNT = 500
DEFAULT_PAGE_SIZE = 10

# Bodies are fetched whole (no server-side preview field exists in IMAP), so
# read_email truncates very large ones rather than returning a megabyte of text.
MAX_BODY_CHARS = 50_000
