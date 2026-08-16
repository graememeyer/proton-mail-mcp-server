import sys

from server import mcp
from config import settings

# Imported for their side effect: the @mcp.tool decorators register the handlers
# on `mcp` at import time. Nothing here is referenced by name.
import auth.tools  # noqa: F401
import mail.tools  # noqa: F401

if __name__ == "__main__":
    transport = settings.MCP_TRANSPORT.lower()

    if transport in ("http", "streamable-http"):
        # Hosted deployment behind an authentik-fronted reverse proxy. Bind to
        # MCP_HOST/MCP_PORT so the proxy can reach it; it should stay on
        # loopback so nothing can bypass the proxy's authentication.
        print(
            f"Starting Proton Mail MCP Server (HTTP) on "
            f"{settings.MCP_HOST}:{settings.MCP_PORT}{settings.MCP_PATH} ...",
            file=sys.stderr,
        )
        mcp.run(
            transport="http",
            host=settings.MCP_HOST,
            port=settings.MCP_PORT,
            path=settings.MCP_PATH,
        )
    else:
        # Local use: stdio transport spawned by the MCP client. Startup logging
        # must go to stderr so it can't corrupt the JSON-RPC stream on stdout.
        print("Starting Proton Mail MCP Server (stdio)...", file=sys.stderr)
        mcp.run()
