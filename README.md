# Smartschool MCP Server

<!-- mcp-name: io.github.MauroDruwel/smartschool-mcp -->

[![CI](https://github.com/MauroDruwel/Smartschool-MCP/actions/workflows/ci.yml/badge.svg)](https://github.com/MauroDruwel/Smartschool-MCP/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/MauroDruwel/Smartschool-MCP/branch/main/graph/badge.svg)](https://codecov.io/gh/MauroDruwel/Smartschool-MCP)
[![PyPI version](https://img.shields.io/pypi/v/smartschool-mcp)](https://pypi.org/project/smartschool-mcp/)
[![License: MIT](https://img.shields.io/github/license/MauroDruwel/Smartschool-MCP)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/downloads/)

Connect Claude (and other MCP clients) to your Smartschool account — ask about grades, assignments, messages, and your schedule in plain language.

## Claude Code plugin

[`claude-plugin/`](claude-plugin/) is a downloadable Claude Code plugin (skill + local scripts) for agenda, berichten, and cijfers. It uses the same account file as this MCP server. Install with `claude --plugin-dir ./claude-plugin` after `uv sync`; steps are in [`claude-plugin/README.md`](claude-plugin/README.md). A hosted web demo is out of scope.

## Tools

| Tool | What it does |
|------|-------------|
| `get_courses` | List enrolled courses with teacher info |
| `get_course_documents` | Documenten per course (TopNav id) or the course list that holds those ids |
| `download_course_document` | Download one file from a course Documenten folder |
| `get_results` | Grades with optional filtering, pagination, and statistics |
| `get_future_tasks` | Upcoming assignments organised by date |
| `get_messages` | Inbox/sent/trash with search, sender filter, and body retrieval |
| `get_schedule` | Day Planner calendar by offset (0 = today, 1 = tomorrow, …) |
| `get_periods` | Academic terms for the current school year |
| `get_reports` | Available report cards |
| `get_planned_elements` | Planner calendar for a date range (optional `types` / `includes`), including element id, description, and upload folders when the payload has them |
| `get_planner_attachments` | Upload folders and files for one planner item (`includes=upload-folders`; live JSON shape not verified) |
| `download_planner_file` | Download a planner file when the payload includes a same-host URL |
| `get_student_support_links` | School support resources and links |
| `get_children` | Linked children on Mijn kinderen (parent/co-account) |
| `switch_child` | Switch the session to another linked child |
| `get_attachments` | List attachments for a specific message |
| `download_attachment` | Download a specific attachment by message and file ID |
| `get_homepage_blocks` | "In de kijker" blocks pinned to the homepage (e.g. monthly menu, calendar) |
| `download_homepage_image` | Download an image embedded in a homepage block |

## Claude Desktop extension

Parents install a one-click `.mcpb` from the **MCPB** GitHub Actions artifact (`smartschool-mcp-mcpb`). The Dutch steps, test questions, and login-lockout reset are in [`docs/claude-desktop-test.md`](docs/claude-desktop-test.md).

The bundle vendors `markminnoye/smartschool` at `517de70` and starts the existing stdio server with `uv`. Claude Desktop's Configure dialog asks for school, username, password, the child's birth date, and the child's name. On startup those values are saved once into the shared account file (`~/.config/smartschool/credentials.json`, or `$GROK_PLUGIN_DATA/credentials.json`; on macOS the password and birth date go to the Keychain). If that profile is already stored, the store wins and the dialog is not copied again. The child name on the profile is what `get_children` / `switch_child` should select. A failed login writes `~/.cache/smartschool/<subdomain>/<user>/auth_failed` after one password POST — the same marker as the Claude plugin. Saving does not log in.

## Quick start — Claude Desktop

Save the account once in `~/.config/smartschool/credentials.json` (the same file the Claude plugin and Grok plugin use). The server reads it when the process has no `SMARTSCHOOL_USERNAME` and `SMARTSCHOOL_PASSWORD`. Set `GROK_PLUGIN_DATA` to store that file in another directory. `SMARTSCHOOL_PROFILE` picks one login when several are saved. Each profile's `children` list stores a name and, when known, the `account_id` and `platform` returned by `get_children`.

```bash
uvx mcp install smartschool-mcp
```

Or add it manually to `claude_desktop_config.json` without a second copy of the password:

```json
{
  "mcpServers": {
    "smartschool": {
      "command": "uvx",
      "args": ["smartschool-mcp"]
    }
  }
}
```

A complete `SMARTSCHOOL_*` environment overrides that one process. It is not written back into the file.

Config file locations: `%APPDATA%\Claude\claude_desktop_config.json` (Windows) · `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) · `~/.config/Claude/claude_desktop_config.json` (Linux)

## Remote / claude.ai

The server supports **Streamable HTTP** transport for use as a remote integration on claude.ai.

### Comparing modes

| Mode | Best for | Setup | Credentials | Auth method |
|------|----------|-------|-------------|-------------|
| **Single-user** | Personal use or one household | Simple, local | In env vars | Optional static Bearer token |
| **Universal** | Hosting for multiple users | Requires public URL + HTTPS | Via login form | OAuth 2.1 with login form |

### Single-user mode

One server instance. The account comes from `~/.config/smartschool/credentials.json`, unless this process already has `SMARTSCHOOL_USERNAME` and `SMARTSCHOOL_PASSWORD`:

```bash
export MCP_API_KEY="a-long-random-secret"   # optional but recommended

smartschool-mcp --transport streamable-http --host 0.0.0.0 --port 8000
```

Add to claude.ai → Settings → Integrations:
- **URL:** `https://your-domain.example.com/mcp`
- **Authorization header:** `Bearer <your MCP_API_KEY>` (if set)

### Universal mode — OAuth 2.1 login flow

One hosted server instance serves **any** Smartschool user via OAuth 2.1. Users authenticate through a browser-based login form during the authorization flow.

```bash
export MCP_ISSUER_URL="https://your-domain.example.com"  # public server URL

smartschool-mcp --transport streamable-http --universal \
  --issuer-url "$MCP_ISSUER_URL" \
  --host 0.0.0.0 --port 8000
```

In claude.ai → Settings → Integrations → Add custom integration:
- **URL:** `https://your-domain.example.com/mcp`

**How it works:**

1. Claude.ai discovers OAuth endpoints at `https://your-domain.example.com/.well-known/oauth-authorization-server`
2. Claude.ai registers a client dynamically via `/register`
3. Claude.ai directs the user to `/authorize?...` (OAuth authorization endpoint)
4. User is redirected to a login form at `/smartschool-login`
5. User enters: **School URL**, **Username**, **Password**, and optional **MFA** (date of birth)
6. On successful login, the server generates an authorization code
7. Claude.ai exchanges the code for an access token (via `/token` with PKCE)
8. Claude.ai uses the Bearer token on all subsequent `/mcp` requests

> **Security:** Credentials are never stored in URLs or environment variables. They're collected via HTTPS form submission and validated against Smartschool. Only access tokens are sent with API requests.

### Making the server publicly accessible

**Required for universal mode.** Claude.ai requires HTTPS and must be able to reach your server to redirect users to the login form and receive authorization callbacks.

Some options:

| Option | Command |
|--------|---------|
| Cloudflare Tunnel | `cloudflared tunnel --url http://localhost:8000` |
| ngrok | `ngrok http 8000` |
| VPS | nginx / Caddy with a Let's Encrypt cert |

After setting up the tunnel/proxy, your server will be reachable at `https://your-domain.example.com`. Use this as `MCP_ISSUER_URL`.

## Environment variables

| Variable | CLI flag | Default | Description |
|----------|----------|---------|-------------|
| `MCP_TRANSPORT` | `--transport` | `stdio` | `stdio` or `streamable-http` |
| `MCP_HOST` | `--host` | `0.0.0.0` | Bind address (HTTP only) |
| `MCP_PORT` | `--port` | `8000` | Port (HTTP only) |
| `MCP_API_KEY` | — | — | Static Bearer token (single-user mode only) |
| `MCP_UNIVERSAL` | `--universal` | off | Enable universal mode (set to `1`, `true`, or `yes`) |
| `MCP_ISSUER_URL` | `--issuer-url` | — | **Required in universal mode.** Public URL of the server, e.g. `https://mcp.example.com` |
| `SESSION_TTL_SECONDS` | — | `3600` | How long to cache Smartschool sessions (universal mode) |
| `SMARTSCHOOL_USERNAME` | — | — | Process override for the username. Otherwise the shared credentials file is used |
| `SMARTSCHOOL_PASSWORD` | — | — | Process override for the password |
| `SMARTSCHOOL_MAIN_URL` | — | — | Process override for the school host |
| `SMARTSCHOOL_MFA` | — | — | Process override for the child birth date `YYYY-MM-DD` |
| `SMARTSCHOOL_PROFILE` | — | — | Which saved login to use (`dering`, `dering:user`, or `user@dering`) |
| `GROK_PLUGIN_DATA` | — | — | Directory for `credentials.json` instead of `~/.config/smartschool/` |

## Contributing

PRs are welcome. Run `uv sync --extra dev` to install dev dependencies, then `uv run pytest` / `uv run ruff check .` / `uv run mypy smartschool_mcp/` before submitting.

## Disclaimer

Unofficial tool, not affiliated with Smartschool. Use in accordance with your school's terms of service.
