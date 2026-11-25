"""
Main entry point for the Smartschool MCP server.
Can be run as: python -m smartschool_mcp or smartschool-mcp

Supports two transports:
  stdio             (default) - for Claude Desktop / local MCP clients
  streamable-http             - for remote MCP clients such as claude.ai

Environment variables (all overridable by CLI flags):
  MCP_TRANSPORT   stdio | streamable-http  (default: stdio)
  MCP_HOST        bind address for HTTP    (default: 0.0.0.0)
  MCP_PORT        port for HTTP            (default: 8000)
  MCP_API_KEY     optional Bearer token (single-user mode only)
  MCP_UNIVERSAL   set to 1 to enable universal mode
  MCP_ISSUER_URL  public URL of the server (required for universal mode)

Universal mode (--universal / MCP_UNIVERSAL=1):
  One server instance serves any Smartschool user via OAuth 2.1.
  Users authenticate through a browser-based login form during the
  OAuth authorization flow.  No credentials in query parameters or
  environment variables — the login form collects them securely.

  In claude.ai → Settings → Integrations → Add custom integration,
  point the URL to your server's public address (MCP_ISSUER_URL).
"""

from __future__ import annotations

import argparse
import hmac
import json
import logging
import os
from typing import Any

from smartschool_mcp.server import mcp

_logger = logging.getLogger(__name__)


def main() -> None:
    """Main entry point for the server."""
    parser = argparse.ArgumentParser(
        description="Smartschool MCP Server",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  smartschool-mcp                          # stdio (Claude Desktop)\n"
            "  smartschool-mcp --transport streamable-http          # HTTP on :8000\n"
            "  smartschool-mcp --transport streamable-http --port 9000\n"
            "  MCP_TRANSPORT=streamable-http MCP_API_KEY=secret smartschool-mcp\n"
            "  smartschool-mcp --transport streamable-http --universal \\\n"
            "    --issuer-url https://mcp.example.com\n"
        ),
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http"],
        default=os.environ.get("MCP_TRANSPORT", "stdio"),
        help="Transport protocol (default: stdio)",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("MCP_HOST", "0.0.0.0"),
        help="Bind host for HTTP transport (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=os.environ.get("MCP_PORT", "8000"),
        help="Bind port for HTTP transport (default: 8000)",
    )
    parser.add_argument(
        "--universal",
        action="store_true",
        default=os.environ.get("MCP_UNIVERSAL", "").lower() in ("1", "true", "yes"),
        help="Universal mode: OAuth 2.1 login flow for multi-user access",
    )
    parser.add_argument(
        "--issuer-url",
        default=os.environ.get("MCP_ISSUER_URL"),
        help=(
            "Public URL of the server (required for universal mode). "
            "Example: https://mcp.example.com"
        ),
    )
    args = parser.parse_args()

    if args.transport == "streamable-http":
        _run_http(
            args.host,
            args.port,
            universal=args.universal,
            issuer_url=args.issuer_url,
        )
    else:
        mcp.run()


def _run_http(
    host: str,
    port: int,
    universal: bool = False,
    issuer_url: str | None = None,
) -> None:
    """Run the MCP server over Streamable HTTP (for remote clients / claude.ai).

    The MCP endpoint will be available at:
        http://<host>:<port>/mcp

    When deploying publicly, put this behind a TLS-terminating reverse proxy
    (nginx, Caddy, Traefik, Cloudflare Tunnel, …) so the URL becomes HTTPS.
    Add it to claude.ai under Settings → Integrations → Add custom integration.
    """
    import uvicorn
    from starlette.middleware.cors import CORSMiddleware

    transport_security = None

    if universal:
        if not issuer_url:
            raise SystemExit(
                "Universal mode requires --issuer-url (or MCP_ISSUER_URL). "
                "This must be the public URL where the server is reachable, "
                "e.g. https://mcp.example.com"
            )

        from urllib.parse import urlparse

        from mcp.server.auth.provider import ProviderTokenVerifier
        from mcp.server.auth.settings import (
            AuthSettings,
            ClientRegistrationOptions,
            RevocationOptions,
        )
        from mcp.server.transport_security import TransportSecuritySettings
        from pydantic import AnyHttpUrl

        from smartschool_mcp.auth import SmartschoolOAuthProvider, login_routes

        provider = SmartschoolOAuthProvider(issuer_url=issuer_url)

        # The resource URL is the public MCP endpoint that clients connect to.
        resource_url = issuer_url.rstrip("/") + "/mcp"

        # Configure OAuth on the MCPServer instance before building the ASGI app.
        mcp._auth_server_provider = provider
        mcp._token_verifier = ProviderTokenVerifier(provider)  # type: ignore[arg-type]
        mcp.settings.auth = AuthSettings(
            issuer_url=AnyHttpUrl(issuer_url),
            resource_server_url=AnyHttpUrl(resource_url),
            client_registration_options=ClientRegistrationOptions(enabled=True),
            revocation_options=RevocationOptions(enabled=True),
        )
        mcp._custom_starlette_routes.extend(login_routes(provider))

        # Allow the public hostname in addition to loopback addresses so that
        # requests forwarded by the reverse proxy (with Host: <public-domain>)
        # pass DNS-rebinding protection instead of getting 421.
        public_host = urlparse(issuer_url).netloc
        transport_security = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*", public_host],
        )

        print("[smartschool-mcp] Universal mode enabled (OAuth 2.1)")
        print(f"[smartschool-mcp] Issuer URL: {issuer_url}")
    else:
        # Optional Bearer-token guard.  Set MCP_API_KEY to enable.
        api_key = os.environ.get("MCP_API_KEY")
        if api_key:
            print(
                "[smartschool-mcp] Note: Bearer auth (MCP_API_KEY) only works "
                "in non-universal mode."
            )

    # Build the ASGI app.  In universal mode the OAuth routes, bearer-token
    # middleware, and auth-context middleware are wired automatically by
    # MCPServer's streamable_http_app().
    # Stateless + JSON responses are recommended for production HTTP deployments.
    app: Any = mcp.streamable_http_app(
        stateless_http=True,
        json_response=True,
        transport_security=transport_security,
        host=host,
    )

    if not universal:
        # Optional static Bearer-token guard for single-user deployments.
        api_key = os.environ.get("MCP_API_KEY")
        if api_key:
            app = _BearerAuthMiddleware(app, api_key)
            print(
                "[smartschool-mcp] Bearer auth enabled - "
                "set Authorization: Bearer <MCP_API_KEY>"
            )

    # CORS is required so browser-based clients (including claude.ai) can read
    # the Mcp-Session-Id header returned during initialisation.
    # Wrap outermost so CORS preflight OPTIONS is handled before auth.
    app = CORSMiddleware(
        app,
        allow_origins=["*"],  # tighten to specific origins in production
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["Mcp-Session-Id"],
    )

    print(f"[smartschool-mcp] Listening on http://{host}:{port}/mcp")
    uvicorn.run(app, host=host, port=port)


# ---------------------------------------------------------------------------
# Helpers shared by middleware
# ---------------------------------------------------------------------------


def _parse_auth_header(scope: dict) -> bytes:
    """Extract the raw Authorization header bytes from an ASGI scope."""
    headers: dict[bytes, bytes] = {k.lower(): v for k, v in scope.get("headers", [])}
    return headers.get(b"authorization", b"")


async def _send_401(send: Any, www_authenticate: bytes, message: str) -> None:
    """Send a JSON 401 response with the given WWW-Authenticate challenge."""
    body = json.dumps({"error": message}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [
                [b"content-type", b"application/json"],
                [b"www-authenticate", www_authenticate],
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


# ---------------------------------------------------------------------------
# Bearer-token middleware (single-user / non-universal mode)
# ---------------------------------------------------------------------------


class _BearerAuthMiddleware:
    """Minimal ASGI middleware that enforces a static Bearer token."""

    def __init__(self, app: Any, token: str) -> None:
        self.app = app
        self.token = token.encode()

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        if scope.get("method", "").upper() == "OPTIONS":
            await self.app(scope, receive, send)
            return

        auth_bytes = _parse_auth_header(scope)
        if not (
            auth_bytes[:7].lower() == b"bearer "
            and hmac.compare_digest(auth_bytes[7:], self.token)
        ):
            await _send_401(send, b'Bearer realm="Smartschool MCP"', "Unauthorized")
            return

        await self.app(scope, receive, send)


if __name__ == "__main__":
    main()
