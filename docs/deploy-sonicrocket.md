# Deploy: Sonic Rocket demo (Fly.io + Cloudflare)

Single-user Streamable HTTP MCP for [SR-53](https://linear.app/sonicrocket/issue/SR-53/live-demo-smartschool-op-sonicrocketapp). Not universal/OAuth.

**Public MCP URL:** `https://smartschool-mcp.sonicrocket.app/mcp`

Clients must send:

```http
Authorization: Bearer <MCP_API_KEY>
```

## What this deploy is

| | |
|---|---|
| Transport | `streamable-http` on `/mcp` |
| Bind | `0.0.0.0:8000` (Fly HTTP service, HTTPS at the edge) |
| Auth | Static Bearer via `MCP_API_KEY` |
| Credentials | One Smartschool account, injected as Fly secrets |
| Region | `ams` (Belgium / EU) |
| Cost | `min_machines_running = 0`, auto-stop when idle |

Do **not** bake `SMARTSCHOOL_*` or `MCP_API_KEY` into the image, `fly.toml`, or git.

## Prerequisites

- [flyctl](https://fly.io/docs/flyctl/install/) logged in (`fly auth login`)
- Permission to create the Fly app `smartschool-mcp-sonicrocket` (or another unused name)
- Cloudflare access for `sonicrocket.app`

## First-time launch

This repo already has `Dockerfile` and `fly.toml`. Create the app from that config **without** deploying until secrets are set:

```bash
fly launch --copy-config --name smartschool-mcp-sonicrocket --region ams --no-deploy
```

If the app already exists:

```bash
fly deploy --ha=false
```

`--ha=false` keeps a single Machine (cheap demo). `fly launch` without `--copy-config` will try to regenerate `fly.toml` — skip that.

## Secrets (required)

Set these on the Fly app only:

```bash
fly secrets set \
  SMARTSCHOOL_USERNAME="your_username" \
  SMARTSCHOOL_PASSWORD="your_password" \
  SMARTSCHOOL_MAIN_URL="your-school.smartschool.be" \
  MCP_API_KEY="$(openssl rand -hex 32)"
```

Optional, if the account uses date-of-birth verification:

```bash
fly secrets set SMARTSCHOOL_MFA="YYYY-MM-DD"
```

Non-secret HTTP defaults (`MCP_TRANSPORT`, `MCP_HOST`, `MCP_PORT`) live in `fly.toml` `[env]` and in the image `CMD`. Do not put passwords or `MCP_API_KEY` in `[env]`.

List what is set (values are hidden):

```bash
fly secrets list
```

## Deploy

```bash
fly deploy --ha=false
```

Confirm the process is up:

```bash
fly status
fly logs
```

The container entrypoint is the `smartschool-mcp` console script. It should log:

```text
[smartschool-mcp] Listening on http://0.0.0.0:8000/mcp
[smartschool-mcp] Bearer auth enabled - set Authorization: Bearer <MCP_API_KEY>
```

## Cloudflare DNS

1. Request a Fly certificate for the public hostname:

   ```bash
   fly certs add smartschool-mcp.sonicrocket.app
   fly certs show smartschool-mcp.sonicrocket.app
   ```

2. In Cloudflare DNS for `sonicrocket.app`:

   | Type | Name | Target | Proxy |
   |------|------|--------|--------|
   | CNAME | `smartschool-mcp` | `smartschool-mcp-sonicrocket.fly.dev` | Proxied (orange cloud) |

3. SSL/TLS mode: **Full (strict)** once the Fly cert is issued.

Let's Encrypt HTTP-01 often fails while the record is proxied. If `fly certs` stays pending, switch the CNAME to **DNS only** until the cert is ready, then proxy again. Alternatively complete the DNS (TXT) challenge Fly prints.

## MCP clients

- **URL:** `https://smartschool-mcp.sonicrocket.app/mcp`
- **Authorization:** `Bearer <MCP_API_KEY>` (the same value as `fly secrets set MCP_API_KEY=...`)

Claude.ai: Settings → Integrations → Add custom integration. Cursor and other Streamable HTTP clients use the same URL + header.

A request without the Bearer token returns **401**. That is expected; it is not a health endpoint.

## Health checks and idle stop

There is no `/health`. `GET /mcp` is 401 when `MCP_API_KEY` is set, so Fly HTTP checks would fail. `fly.toml` uses a **TCP** check on port 8000.

`min_machines_running = 0` and `auto_stop_machines = "stop"` mean the Machine stops when idle. The first request after idle cold-starts the VM (a few seconds).

## Local image (optional)

Does not require Fly. Do not pass real secrets as `ARG`/`ENV` in the Dockerfile.

```bash
docker build -t smartschool-mcp .
docker run --rm -p 8000:8000 \
  -e SMARTSCHOOL_USERNAME \
  -e SMARTSCHOOL_PASSWORD \
  -e SMARTSCHOOL_MAIN_URL \
  -e SMARTSCHOOL_MFA \
  -e MCP_API_KEY \
  smartschool-mcp
```

The lockfile pins `smartschool` to a public GitHub revision, so the **build** needs network access to GitHub + PyPI. Runtime needs outbound HTTPS to the school Smartschool host.
