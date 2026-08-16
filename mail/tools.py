"""
Mail tools for the Proton Mail MCP server.

Importing this module is what registers the tools with the FastMCP instance --
each handler is decorated with @mcp.tool at definition time.
"""

from .list import handle_list_emails, handle_list_folders
from .manage import handle_mark_email, handle_move_email
from .read import handle_read_email
from .search import handle_search_emails
from .send import handle_send_email

__all__ = [
    "handle_list_emails",
    "handle_list_folders",
    "handle_search_emails",
    "handle_read_email",
    "handle_send_email",
    "handle_mark_email",
    "handle_move_email",
]
