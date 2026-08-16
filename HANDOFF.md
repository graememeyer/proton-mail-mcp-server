# Handoff: hosting the Proton Mail MCP server on Proxmox (LXC)

For the Claude instance running on the Proxmox home server. The **application is
done and tested**; **deployment/ops is yours**. This mirrors the
`outlook-mcp-server` deployment deliberately — same shape, same runner contract,
same authentik pattern — so lean on whatever you already built there.

## Repo state

- `github.com/graememeyer/proton-mail-mcp`, branch `main`.
- Standalone **FastMCP 3.x**, **imap-tools 1.15**, Python **3.10+**.
- 97 offline tests pass (`pytest -q`), no Bridge or network needed. CI runs them
  on `ubuntu-latest` and gates the deploy job on them.

## The one big difference from the Outlook server

Outlook talks to Graph directly and the app holds an OAuth token cache. **This
server holds no Proton credentials at all.** Proton Mail is end-to-end encrypted
and has no third-party API; the only supported route in is **Proton Bridge**,
which signs in to the account, does all the OpenPGP work locally, and re-exposes
the decrypted mailbox as plain IMAP/SMTP on loopback.

So there are **two daemons** in this container, not one:

```
proton-mcp.service     FastMCP, HTTP 127.0.0.1:8000/mcp   <- deployed by CI
proton-bridge.service  IMAP 127.0.0.1:1143, SMTP 1025     <- set up once, by hand
```

Bridge is the stateful one. It is the thing that will break, and CI never
touches it.

## Two independent auth layers (unchanged from Outlook)

1. **Connector auth** — authentik (OAuth 2.1/OIDC) decides *who may use the
   connector*. The **reverse proxy (forward-auth) enforces it**; the MCP server
   binds to loopback and has **no auth code**, by design. Claude.ai accepts a
   pre-registered client id/secret (no DCR needed).
2. **Mailbox auth** — Proton Bridge's own sign-in to the Proton account, seeded
   once by hand. Signing in to the connector does NOT grant mailbox access.

## App configuration (env vars the server reads)

| Var | Value for container | Notes |
|---|---|---|
| `MCP_TRANSPORT` | `http` | default is `stdio` (local) |
| `MCP_HOST` / `MCP_PORT` / `MCP_PATH` | `127.0.0.1` / `8000` / `/mcp` | proxy fronts it |
| `PROTON_BRIDGE_USER` | the Proton address | from `bridge --cli` → `info` |
| `PROTON_BRIDGE_PASSWORD` | Bridge's generated password | **secret**; not the Proton password |
| `PROTON_BRIDGE_HOST` | `127.0.0.1` | Bridge in the same container |
| `PROTON_IMAP_PORT` / `PROTON_SMTP_PORT` | `1143` / `1025` | Bridge defaults |
| `PROTON_IMAP_SECURITY` / `PROTON_SMTP_SECURITY` | `starttls` | Bridge default on both |
| `PROTON_TLS_VERIFY` | `false` | Bridge's cert is self-signed by its own CA |

`PROTON_BRIDGE_PASSWORD` is the only secret. Put the env file at
`/etc/proton-mcp/proton-mcp.env`, mode `0640`, owned by the service user.

## Bridge setup — the actual work

This is the part with teeth. Headless Bridge on Linux has three known traps:

1. **Bridge needs a password manager.** It refuses to start without
   `gnome-keyring` or `pass`. On a headless box use `pass`, backed by a
   **passphrase-free** GPG key, or Bridge will block forever waiting for a
   pinentry prompt that nothing can answer:
   ```bash
   gpg --batch --passphrase '' --quick-gen-key 'ProtonMail Bridge' default default never
   pass init "ProtonMail Bridge"
   ```
2. **The official `.deb` drags in a pile of graphical dependencies.** Either
   build the no-GUI binary (`make build-nogui`) or use a headless container
   image. Don't install the desktop package in an LXC.
3. **Login is interactive, once.** There is no env-var or flag route:
   ```bash
   protonmail-bridge --cli
   >>> login          # address, password, 2FA, mailbox password
   >>> info           # prints the IMAP/SMTP ports and the GENERATED password
   >>> exit
   ```
   Copy the generated password into `PROTON_BRIDGE_PASSWORD`. It is a per-install
   mail-client password, unrelated to the Proton account password.

Then run it as a service with `--noninteractive`:

```ini
[Unit]
Description=Proton Mail Bridge
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=proton-bridge
Group=proton-bridge
ExecStart=/usr/local/bin/protonmail-bridge --noninteractive
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

**Persist Bridge's state directory** (`~/.config/protonmail/bridge-v3` and the
`pass`/GPG store) across container rebuilds, or you re-do the interactive login
and the generated password changes — which silently breaks the MCP server until
the env file is updated too.

Note: some headless-Bridge guides patch `constants.go` to bind `0.0.0.0`.
**Don't.** The MCP server is in the same container; loopback is correct and
keeps the decrypted mailbox off the network.

## Reference: MCP server unit

```ini
[Unit]
Description=Proton Mail MCP Server
After=network-online.target proton-bridge.service
Wants=network-online.target
Requires=proton-bridge.service

[Service]
Type=simple
User=proton-mcp
Group=proton-mcp
WorkingDirectory=/opt/proton-mcp/app
EnvironmentFile=/etc/proton-mcp/proton-mcp.env
ExecStart=/opt/proton-mcp/venv/bin/python /opt/proton-mcp/app/main.py
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true

[Install]
WantedBy=multi-user.target
```

`Requires=`/`After=` on the Bridge unit means a Bridge restart doesn't leave the
MCP server pointing at a dead socket on boot. It does not cover Bridge crashing
later — the server reconnects on its own for that (see below).

## Reference: deploy flow

The CI workflow (`.github/workflows/deploy.yml`) already encodes this and needs
the same runner contract as the Outlook deploy:

1. Runner rsyncs the repo to `/opt/proton-mcp/app`, builds the venv at
   `/opt/proton-mcp/venv`, `pip install -r requirements.txt`.
2. `sudo /usr/bin/systemctl restart proton-mcp.service` — pin exactly this in
   `/etc/sudoers.d/gha`, as with `outlook-mcp.service`. Nothing else may sudo.
3. Health-gates on the MCP `initialize` handshake returning 200:
   ```bash
   curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:8000/mcp \
     -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
     -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"t","version":"0"}}}'
   ```
4. Reverse proxy + authentik forward-auth for the chosen hostname →
   `127.0.0.1:8000` (path `/mcp`).
5. Add the custom connector in Claude.ai with the authentik client id/secret.

Note the health check only proves the MCP server is up — it says nothing about
Bridge. For that, call the `check_auth_status` tool, which reports the account
and INBOX counts or names the exact failure.

## Behaviour worth knowing before you debug it

- **The IMAP connection is pooled and self-healing.** One connection, serialised
  behind a lock, NOOP-probed on checkout and transparently reopened if Bridge
  restarted. You should not need to restart the MCP server after bouncing
  Bridge.
- **Every tool degrades to a sentence, not a stack trace.** Bridge down, bad
  credentials, TLS failure and unknown folders each produce a specific message
  naming the fix. Verified with Bridge unreachable.
- **Nothing marks mail as read** unless explicitly asked (`BODY.PEEK` throughout;
  `read_email` has an opt-in `mark_as_read`).
- **No permanent-delete tool exists.** `move_email` to `trash` is the strongest
  destructive action available.

## Decisions already made (don't re-litigate unless you have reason)

- **Bridge over a direct Proton API port.** The Go SDK (`go-proton-api`) is
  official but is Bridge's own internals: it needs a whitelisted app version and
  hits CAPTCHA/human-verification on unfamiliar logins, which is untenable for an
  unattended service. The only Python option is a third-party reimplementation
  that bundles OpenCV to solve those CAPTCHAs. Bridge is the supported path and
  keeps all the fragile crypto in Proton's own code. Requires a paid plan, which
  this account has.
- LXD-native + systemd (no Docker-in-LXC), matching the Outlook deployment.
- authentik forward-auth at the proxy; the server has no token-validation code.
- Bridge and the MCP server in the same container, talking over loopback.

## Verified vs not

- **Verified from the workstation:** all 9 tools register with FastMCP; HTTP
  transport returns 200 to `initialize`; 97 offline tests pass; every tool's
  failure path returns a readable message with Bridge unreachable. Message
  parsing, filtering, sorting and rendering are tested against real RFC822
  messages.
- **NOT verified — no Bridge instance was available:** anything requiring a live
  mailbox. Specifically: that Bridge's folder names match what `pick_folder`
  expects (`Folders/…`, `Labels/…`, `All Mail`), that STARTTLS negotiates
  against Bridge's self-signed cert, that SMTP submission is accepted with the
  `From` header set to the Bridge user, and that Bridge's `SEARCH` handles the
  criteria the search tool builds. **Run `check_auth_status` and then
  `list_folders` first** — between them they exercise connection, TLS, auth and
  folder naming, which is where a mismatch would show up.
- NOT verified (needs your environment): systemd units, the runner deploy, the
  headless Bridge login, authentik/proxy wiring, the Claude.ai connector.
