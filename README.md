# Proton Mail MCP Server

An MCP server exposing a Proton Mail mailbox to Claude — list, search, read,
send, and file messages.

Proton Mail has no third-party REST API. Everything in a Proton mailbox is
end-to-end encrypted, and the only supported way for an external program to
reach it is [Proton Bridge](https://proton.me/mail/bridge), which signs in to
the account, performs all OpenPGP encryption and decryption locally, and
re-exposes the decrypted mailbox as an ordinary IMAP/SMTP server on loopback.

This server therefore talks IMAP and SMTP to Bridge. There is no Proton protocol
code here at all — no SRP, no key handling, no PGP — which is deliberate: those
are the parts that break when Proton changes something.

```
Claude.ai
   │
   ▼  (authentik forward-auth at the reverse proxy)
proton-mail-mcp        FastMCP, HTTP on 127.0.0.1:8000/mcp
   │  IMAP 127.0.0.1:1143   (STARTTLS)
   │  SMTP 127.0.0.1:1025   (STARTTLS)
   ▼
Proton Bridge          SRP login + OpenPGP, Proton's own code
   │
   ▼
Proton
```

**Bridge requires a paid Proton plan** (Mail Plus or higher).

## Tools

| Tool | What it does |
|---|---|
| `list_emails` | Recent messages in a folder, newest first, with optional date window and unread filter |
| `search_emails` | Search by sender, recipient, subject, body, free text, date range or attachments |
| `read_email` | One message in full, with the body flattened to text |
| `send_email` | Send a message, optionally as a threaded reply or from another address on the account |
| `list_folders` | The mailbox's system folders, user folders and labels, with counts |
| `mark_email` | Mark a message read or unread |
| `move_email` | Move a message to another folder (moving to `trash` is how you delete) |
| `about` | What this server is |
| `check_auth_status` | Whether the mailbox is reachable through Bridge right now |

Listing, searching and reading **never mark mail as read** — the IMAP fetches use
`BODY.PEEK`. `read_email` can opt in with `mark_as_read=true`.

### Message IDs

Tools return IDs of the form `<folder>:<uid>`, e.g. `INBOX:4821` or
`Folders/Work:77`. IMAP UIDs are per-folder, so an ID is only valid while the
message stays where it was found; after `move_email` the old ID is dead and the
tool says so.

### Folder names

Bridge puts Proton's system folders at the top level (`INBOX`, `Sent`, `Drafts`,
`Archive`, `Spam`, `Trash`, `All Mail`) and namespaces user-created ones under
`Folders/` (exclusive) and `Labels/` (non-exclusive). Tools accept any
reasonable spelling: `inbox`, `junk` → `Spam`, `bin` → `Trash`, `all mail`, a
plain leaf name like `Work` → `Folders/Work`, or the full path. Search a whole
mailbox with `folder="all mail"`.

## Setup

### 1. Proton Bridge

Install Bridge and sign in once, out of band. Headless:

```bash
protonmail-bridge --cli
>>> login
>>> info      # prints the address, ports, and the generated password
```

The password Bridge prints is **not** the Proton account password — it is
generated per install for mail clients, and it is what goes in the config below.

### 2. Configure

```bash
cp .env.example .env
# fill in PROTON_BRIDGE_USER and PROTON_BRIDGE_PASSWORD
```

| Variable | Default | Notes |
|---|---|---|
| `PROTON_BRIDGE_USER` | *required* | Your Proton address |
| `PROTON_BRIDGE_PASSWORD` | *required* | Bridge's generated mail-client password |
| `PROTON_BRIDGE_HOST` | `127.0.0.1` | |
| `PROTON_IMAP_PORT` | `1143` | |
| `PROTON_SMTP_PORT` | `1025` | |
| `PROTON_IMAP_SECURITY` | `starttls` | or `ssl`, `none` |
| `PROTON_SMTP_SECURITY` | `starttls` | or `ssl`, `none` |
| `PROTON_TLS_VERIFY` | `false` | Bridge's cert is self-signed by its own CA |
| `PROTON_SEND_FROM` | = `PROTON_BRIDGE_USER` | From header, if sending as another address |
| `PROTON_TIMEOUT` | `60` | Seconds |
| `MCP_TRANSPORT` | `stdio` | `http` for the hosted deployment |
| `MCP_HOST` / `MCP_PORT` / `MCP_PATH` | `127.0.0.1` / `8000` / `/mcp` | HTTP transport only |

### 3. Install and run

```bash
python -m venv venv
venv/bin/pip install -r requirements.txt
venv/bin/python main.py
```

Check it can see the mailbox by calling the `check_auth_status` tool, which
reports the account and INBOX counts, or names the specific problem if not.

### Local use with Claude Code / Desktop

```json
{
  "mcpServers": {
    "proton-assistant": {
      "command": "/path/to/proton-mail-mcp/venv/bin/python",
      "args": ["/path/to/proton-mail-mcp/main.py"]
    }
  }
}
```

Credentials come from `.env`; keep them out of the client config.

## Hosted deployment

Runs on a Proxmox LXC behind a reverse proxy with authentik forward-auth,
deployed by a GitHub Actions self-hosted runner on push to `main`. Two
independent auth layers, neither of which is this server's code:

1. **Connector auth** — authentik decides who may use the connector, enforced at
   the proxy. The server binds to loopback and has no auth code by design.
2. **Mailbox auth** — Bridge holds the Proton credentials, signed in once.

Signing in to the connector does not grant mailbox access; they are unrelated.

See `HANDOFF.md` for the deployment runbook.

## Tests

```bash
venv/bin/python -m pytest
```

The suite is fully offline — no Bridge, no credentials, no network. Message
handling is tested against real `imap_tools` objects parsed from raw RFC822
bytes, so parsing, filtering, sorting and rendering are exercised for real.

## Limitations

- **Search is literal.** IMAP `SEARCH` does case-insensitive substring matching,
  not ranked relevance. There is no stemming and no scoring.
- **Date filters are day-granular on the server.** IMAP `SINCE`/`BEFORE` compare
  whole dates, so a time-of-day bound is applied client-side after fetching.
- **Attachment filtering happens after the fetch**, because IMAP cannot search on
  it. Summaries infer attachments from `Content-Type: multipart/mixed`.
- **No attachment download.** Attachments are listed by name and size only.
- **Bridge must be running.** If it isn't, every tool says so plainly rather than
  failing obscurely.
