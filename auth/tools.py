"""
Server-information and connection-status tools.

There is no sign-in flow here, unlike the Outlook server this is modelled on.
Two separate layers own authentication and neither is the MCP server's job:

  * who may call the connector -- authentik, enforced by the reverse proxy in
    front of this process;
  * access to the mailbox itself -- Proton Bridge, which holds the account
    credentials and is signed in once, out of band.

So "check_auth_status" answers "can I reach the mailbox right now", which is the
only question this process can actually answer.
"""

__all__ = ["handle_about", "handle_check_auth_status"]

import asyncio

from config import settings
from server import mcp
from mail.client import check_connection


@mcp.tool(name="about", title="About this server")
async def handle_about() -> str:
    """
    Describe this MCP server and what it can do

    Returns:
        Server name, version, and the mail operations it exposes
    """
    return (
        f"Proton Mail MCP Server {settings.SERVER_VERSION}\n\n"
        f"Provides access to a Proton Mail mailbox through Proton Bridge, which "
        f"handles the Proton account login and all OpenPGP encryption and "
        f"decryption locally.\n\n"
        f"Tools: list_emails, search_emails, read_email, send_email, "
        f"list_folders, mark_email, move_email.\n\n"
        f"Message IDs are '<folder>:<uid>' and are only valid while the message "
        f"stays in that folder."
    )


@mcp.tool(name="check_auth_status", title="Check mailbox connection")
async def handle_check_auth_status() -> str:
    """
    Check whether the mailbox is reachable through Proton Bridge

    Returns:
        The connection state, the account in use and INBOX counts when healthy;
        otherwise what went wrong and how to fix it
    """
    return await asyncio.to_thread(check_connection)
