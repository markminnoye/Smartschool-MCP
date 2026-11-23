"""OAuth 2.1 provider for Smartschool MCP universal mode.

Implements ``OAuthAuthorizationServerProvider`` so that FastMCP's built-in
OAuth machinery handles discovery, client registration, authorization code
flow with PKCE, token exchange, and Bearer-token validation automatically.

The provider stores all state in memory (TTL-bounded caches).  It is designed
for single-process deployments; horizontal scaling would require an external
store (Redis, DB, …).
"""

from __future__ import annotations

import html
import logging
import os
import re
import secrets
import time
from typing import NamedTuple
from urllib.parse import urlparse

import anyio.to_thread
import requests
from cachetools import TTLCache
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    RefreshToken,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from smartschool import AppCredentials
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.routing import Route

from smartschool_mcp.guard import (
    MemoryGuardedSession,
    memory_lockout_active,
    memory_lockout_message,
)

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Hostname validation (moved from __main__.py)
# ---------------------------------------------------------------------------

_HOSTNAME_RE = re.compile(
    r"^(?:[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}$"
)


def _parse_csv_env(name: str) -> set[str]:
    return {
        entry.strip().lower()
        for entry in os.environ.get(name, "").split(",")
        if entry.strip()
    }


_ALLOWED_HOSTS = _parse_csv_env("SMARTSCHOOL_ALLOWED_HOSTS")
_ALLOWED_SUFFIXES = _parse_csv_env("SMARTSCHOOL_ALLOWED_SUFFIXES") or {
    ".smartschool.be"
}


def _registered_domain(host: str) -> str:
    parts = host.lower().split(".")
    if len(parts) < 2:
        return host.lower()
    return ".".join(parts[-2:])


def _matches_suffix(host: str, suffix: str) -> bool:
    normalized = suffix.lower().strip()
    if not normalized:
        return False
    if normalized.startswith("."):
        bare = normalized[1:]
        return host == bare or host.endswith(normalized)
    return host == normalized or host.endswith(f".{normalized}")


def _is_trusted_school_host(host: str) -> bool:
    lower_host = host.lower()
    if _ALLOWED_HOSTS:
        if lower_host in _ALLOWED_HOSTS:
            return True
        if _registered_domain(lower_host) in _ALLOWED_HOSTS:
            return True
    return any(_matches_suffix(lower_host, suffix) for suffix in _ALLOWED_SUFFIXES)


def _validate_school_url(raw: str) -> str | None:
    """Normalise and validate a school URL.

    Returns a bare ``hostname`` (or ``hostname:port``) string, or ``None``
    if the value does not look like a valid hostname.
    """
    candidate = raw if "://" in raw else f"https://{raw}"
    try:
        parsed = urlparse(candidate)
        host = parsed.hostname or ""
    except Exception:
        return None
    if not host or not _HOSTNAME_RE.match(host):
        return None
    if not _is_trusted_school_host(host):
        return None
    return host if not parsed.port else f"{host}:{parsed.port}"


# ---------------------------------------------------------------------------
# Custom token types — carry a ``cred_key`` that maps to stored credentials
# ---------------------------------------------------------------------------


class SmartschoolAuthCode(AuthorizationCode):
    cred_key: str


class SmartschoolAccessToken(AccessToken):
    cred_key: str


class SmartschoolRefreshToken(RefreshToken):
    cred_key: str


# ---------------------------------------------------------------------------
# Pending auth — stores both AuthorizationParams and the requesting client_id
# ---------------------------------------------------------------------------


class _PendingAuth(NamedTuple):
    params: AuthorizationParams
    client_id: str


# ---------------------------------------------------------------------------
# Public helper for server.py
# ---------------------------------------------------------------------------


def get_credentials(cred_key: str) -> AppCredentials | None:
    """Look up Smartschool credentials by key (used by ``server._session()``)."""
    return _credential_store.get(cred_key)


# ---------------------------------------------------------------------------
# OAuth provider
# ---------------------------------------------------------------------------

# TTL constants (seconds)
_CLIENT_TTL = 86400  # 24 hours
_AUTH_CODE_TTL = 600  # 10 minutes
_ACCESS_TOKEN_TTL = 3600  # 1 hour
_REFRESH_TOKEN_TTL = 86400  # 24 hours
_PENDING_AUTH_TTL = 600  # 10 minutes

# Module-level credential store; populated by the login form.
# Entries live as long as a refresh token, so a client that refreshes after
# a quiet hour still finds the login. Each refresh resets the timer.
# Max 256 entries — mirrors the session cache in server.py.
_credential_store: TTLCache[str, AppCredentials] = TTLCache(
    maxsize=256, ttl=_REFRESH_TOKEN_TTL
)


class SmartschoolOAuthProvider:
    """In-memory OAuth 2.1 authorization server for Smartschool MCP.

    Implements the ``OAuthAuthorizationServerProvider`` protocol expected by
    FastMCP.  All state lives in TTL-bounded caches so stale entries are
    automatically evicted.
    """

    def __init__(self, issuer_url: str) -> None:
        self.issuer_url = str(issuer_url).rstrip("/")

        self._clients: TTLCache[str, OAuthClientInformationFull] = TTLCache(
            maxsize=256, ttl=_CLIENT_TTL
        )
        self._auth_codes: TTLCache[str, SmartschoolAuthCode] = TTLCache(
            maxsize=256, ttl=_AUTH_CODE_TTL
        )
        self._access_tokens: TTLCache[str, SmartschoolAccessToken] = TTLCache(
            maxsize=1024, ttl=_ACCESS_TOKEN_TTL
        )
        self._refresh_tokens: TTLCache[str, SmartschoolRefreshToken] = TTLCache(
            maxsize=1024, ttl=_REFRESH_TOKEN_TTL
        )
        self._pending_auths: TTLCache[str, _PendingAuth] = TTLCache(
            maxsize=256, ttl=_PENDING_AUTH_TTL
        )

    # -- Client registration ------------------------------------------------

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return self._clients.get(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        assert client_info.client_id is not None
        self._clients[client_info.client_id] = client_info

    # -- Authorization ------------------------------------------------------

    async def authorize(
        self,
        client: OAuthClientInformationFull,
        params: AuthorizationParams,
    ) -> str:
        """Store pending auth params and redirect to the built-in login form."""
        pending_id = secrets.token_urlsafe(32)
        assert client.client_id is not None
        self._pending_auths[pending_id] = _PendingAuth(
            params=params, client_id=client.client_id
        )
        return f"{self.issuer_url}/smartschool-login?pending={pending_id}"

    # -- Authorization code -------------------------------------------------

    async def load_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: str,
    ) -> SmartschoolAuthCode | None:
        code_obj = self._auth_codes.get(authorization_code)
        if code_obj is not None and code_obj.client_id != client.client_id:
            return None
        return code_obj

    async def exchange_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: SmartschoolAuthCode,
    ) -> OAuthToken:
        # Atomically consume the one-time code.
        code_obj = self._auth_codes.pop(authorization_code.code, None)
        if code_obj is None or code_obj.client_id != client.client_id:
            raise ValueError("Invalid or already used authorization code")

        cred_key = code_obj.cred_key
        now = int(time.time())
        cid = client.client_id or ""

        access = SmartschoolAccessToken(
            token=secrets.token_urlsafe(32),
            client_id=cid,
            scopes=code_obj.scopes,
            expires_at=now + _ACCESS_TOKEN_TTL,
            cred_key=cred_key,
        )
        refresh = SmartschoolRefreshToken(
            token=secrets.token_urlsafe(32),
            client_id=cid,
            scopes=code_obj.scopes,
            expires_at=now + _REFRESH_TOKEN_TTL,
            cred_key=cred_key,
        )

        self._access_tokens[access.token] = access
        self._refresh_tokens[refresh.token] = refresh

        return OAuthToken(
            access_token=access.token,
            token_type="Bearer",
            expires_in=_ACCESS_TOKEN_TTL,
            scope=" ".join(access.scopes) if access.scopes else None,
            refresh_token=refresh.token,
        )

    # -- Access tokens ------------------------------------------------------

    async def load_access_token(self, token: str) -> SmartschoolAccessToken | None:
        access = self._access_tokens.get(token)
        if access is None:
            return None
        if access.expires_at is not None and access.expires_at < time.time():
            self._access_tokens.pop(token, None)
            return None
        return access

    # -- Refresh tokens -----------------------------------------------------

    async def load_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: str,
    ) -> SmartschoolRefreshToken | None:
        rt = self._refresh_tokens.get(refresh_token)
        if rt is not None and rt.client_id != client.client_id:
            return None
        return rt

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: SmartschoolRefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        # Atomically consume the old refresh token.
        old_refresh = self._refresh_tokens.pop(refresh_token.token, None)
        if old_refresh is None or old_refresh.client_id != client.client_id:
            raise ValueError("Invalid or already used refresh token")

        cred_key = old_refresh.cred_key
        creds = _credential_store.get(cred_key)
        if creds is None:
            raise ValueError(
                "Refresh token credentials expired; re-authentication required"
            )
        _credential_store[cred_key] = creds
        now = int(time.time())
        cid = client.client_id or ""

        new_access = SmartschoolAccessToken(
            token=secrets.token_urlsafe(32),
            client_id=cid,
            scopes=scopes or old_refresh.scopes,
            expires_at=now + _ACCESS_TOKEN_TTL,
            cred_key=cred_key,
        )
        new_refresh = SmartschoolRefreshToken(
            token=secrets.token_urlsafe(32),
            client_id=cid,
            scopes=scopes or old_refresh.scopes,
            expires_at=now + _REFRESH_TOKEN_TTL,
            cred_key=cred_key,
        )

        self._access_tokens[new_access.token] = new_access
        self._refresh_tokens[new_refresh.token] = new_refresh

        return OAuthToken(
            access_token=new_access.token,
            token_type="Bearer",
            expires_in=_ACCESS_TOKEN_TTL,
            scope=" ".join(new_access.scopes) if new_access.scopes else None,
            refresh_token=new_refresh.token,
        )

    # -- Revocation ---------------------------------------------------------

    async def revoke_token(
        self,
        token: SmartschoolAccessToken | SmartschoolRefreshToken,
    ) -> None:
        if isinstance(token, SmartschoolAccessToken):
            self._access_tokens.pop(token.token, None)
        elif isinstance(token, SmartschoolRefreshToken):
            self._refresh_tokens.pop(token.token, None)
            # The client disconnected: forget the stored password now
            # instead of keeping it until the TTL runs out.
            _credential_store.pop(token.cred_key, None)

    # -- Login form helpers (used by route handlers) ------------------------

    def get_pending_auth(self, pending_id: str) -> _PendingAuth | None:
        return self._pending_auths.get(pending_id)

    def complete_authorization(
        self,
        pending_id: str,
        cred_key: str,
    ) -> SmartschoolAuthCode:
        """Create an authorization code after successful login."""
        pending = self._pending_auths.pop(pending_id)
        params = pending.params

        code = SmartschoolAuthCode(
            code=secrets.token_urlsafe(32),
            scopes=params.scopes or [],
            expires_at=time.time() + _AUTH_CODE_TTL,
            client_id=pending.client_id,
            code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            cred_key=cred_key,
        )
        self._auth_codes[code.code] = code
        return code


# ---------------------------------------------------------------------------
# Login form HTML
# ---------------------------------------------------------------------------

_LOGIN_FORM_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Smartschool MCP — Sign In</title>
<style>
  *, *::before, *::after { box-sizing: border-box; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: #f5f5f5; margin: 0; padding: 2rem;
    display: flex; justify-content: center; align-items: flex-start;
  }
  .card {
    background: #fff; border-radius: 12px; box-shadow: 0 2px 12px rgba(0,0,0,.1);
    padding: 2rem; max-width: 420px; width: 100%;
  }
  h1 { font-size: 1.4rem; margin: 0 0 .25rem; }
  p.subtitle { color: #666; font-size: .9rem; margin: 0 0 1.5rem; }
  label { display: block; font-weight: 600; font-size: .85rem; margin-bottom: .3rem; }
  input[type=text], input[type=password] {
    width: 100%; padding: .6rem .75rem; border: 1px solid #ccc; border-radius: 6px;
    font-size: .95rem; margin-bottom: 1rem; transition: border-color .15s;
  }
  input:focus {
    outline: none; border-color: #4a90d9;
    box-shadow: 0 0 0 3px rgba(74,144,217,.15);
  }
  .hint { font-size: .8rem; color: #888; margin: -.75rem 0 1rem; }
  button {
    width: 100%; padding: .7rem; background: #4a90d9; color: #fff; border: none;
    border-radius: 6px; font-size: 1rem; font-weight: 600; cursor: pointer;
    transition: background .15s;
  }
  button:hover { background: #3a7bc8; }
  .error {
    background: #fef2f2; border: 1px solid #fca5a5; color: #991b1b;
    border-radius: 6px; padding: .75rem; margin-bottom: 1rem; font-size: .9rem;
  }
</style>
</head>
<body>
<div class="card">
  <h1>Smartschool MCP</h1>
  <p class="subtitle">Sign in with your Smartschool account
  to authorise this integration.</p>
  {error_html}
  <form method="POST" action="/smartschool-login">
    <input type="hidden" name="pending" value="{pending_id}">

    <label for="school">School URL</label>
    <input type="text" id="school" name="school" placeholder="school.smartschool.be"
           value="{school_value}" required>

    <label for="username">Username</label>
    <input type="text" id="username" name="username" autocomplete="username"
           value="{username_value}" required>

    <label for="password">Password</label>
    <input type="password" id="password" name="password"
           autocomplete="current-password" required>

    <label for="mfa">Date of birth (MFA)</label>
    <input type="text" id="mfa" name="mfa" placeholder="YYYY-MM-DD (optional)"
           value="{mfa_value}">
    <p class="hint">Only required if your school uses date-of-birth verification.</p>

    <button type="submit">Sign in</button>
  </form>
</div>
</body>
</html>
"""


def _render_login_form(
    pending_id: str,
    *,
    error: str = "",
    school: str = "",
    username: str = "",
    mfa: str = "",
) -> HTMLResponse:
    escaped_error = html.escape(error) if error else ""
    escaped_pending = html.escape(pending_id)
    escaped_school = html.escape(school or "")
    escaped_username = html.escape(username or "")
    escaped_mfa = html.escape(mfa or "")
    error_html = f'<div class="error">{escaped_error}</div>' if escaped_error else ""
    rendered_html = (
        _LOGIN_FORM_HTML.replace("{error_html}", error_html)
        .replace("{pending_id}", escaped_pending)
        .replace("{school_value}", escaped_school)
        .replace("{username_value}", escaped_username)
        .replace("{mfa_value}", escaped_mfa)
    )
    return HTMLResponse(rendered_html)


# ---------------------------------------------------------------------------
# Starlette route handlers
# ---------------------------------------------------------------------------


def login_routes(provider: SmartschoolOAuthProvider) -> list[Route]:
    """Return Starlette routes for the Smartschool login form."""

    async def login_handler(request: Request) -> Response:
        if request.method == "GET":
            return await _handle_login_get(request, provider)
        return await _handle_login_post(request, provider)

    return [
        Route(
            "/smartschool-login",
            endpoint=login_handler,
            methods=["GET", "POST"],
        ),
    ]


async def _handle_login_get(
    request: Request,
    provider: SmartschoolOAuthProvider,
) -> Response:
    pending_id = request.query_params.get("pending", "")
    if not pending_id or provider.get_pending_auth(pending_id) is None:
        return HTMLResponse(
            "<h1>400 — Invalid or expired login link</h1>", status_code=400
        )
    return _render_login_form(pending_id)


def _verify_login(creds: AppCredentials) -> None:
    """Log in once and load a page, so bad credentials raise here.

    The login form is public, so a rejected password blocks that username
    for a while in memory (``MemoryGuardedSession``), not with a file that
    only an admin can remove.
    """
    session = MemoryGuardedSession(creds)
    session.get("/?module=Messages&file=messageOverview")


async def _handle_login_post(
    request: Request,
    provider: SmartschoolOAuthProvider,
) -> Response:
    form = await request.form()
    pending_id = str(form.get("pending", ""))
    school_raw = str(form.get("school", ""))
    username = str(form.get("username", ""))
    password = str(form.get("password", ""))
    mfa = str(form.get("mfa", ""))

    # Validate pending auth exists
    pending = provider.get_pending_auth(pending_id)
    if pending is None:
        return HTMLResponse(
            "<h1>400 — Login session expired, please try again</h1>",
            status_code=400,
        )

    # Validate school hostname
    school = _validate_school_url(school_raw)
    if not school:
        return _render_login_form(
            pending_id,
            error="Invalid school URL.",
            school=school_raw,
            username=username,
            mfa=mfa,
        )

    if not username or not password:
        return _render_login_form(
            pending_id,
            error="Username and password are required.",
            school=school_raw,
            username=username,
            mfa=mfa,
        )

    if memory_lockout_active(username, school):
        return _render_login_form(
            pending_id,
            error=memory_lockout_message(),
            school=school_raw,
            username=username,
            mfa=mfa,
        )

    # Validate credentials by attempting a Smartschool login.
    # A rejected password blocks this username for a while (in memory).
    creds = AppCredentials(
        username=username, password=password, main_url=school, mfa=mfa
    )
    try:
        # Blocking HTTP to Smartschool: run it in a worker thread so the
        # event loop keeps serving other users while this login is checked.
        await anyio.to_thread.run_sync(_verify_login, creds)
    except Exception as exc:
        _logger.debug(
            "Smartschool login failed for user=%s school=%s", username, school
        )
        if memory_lockout_active(username, school):
            error = memory_lockout_message()
        elif isinstance(exc, requests.RequestException):
            error = "Could not reach Smartschool. Please try again later."
        else:
            error = "Login failed — check your school URL, username, and password."
        return _render_login_form(
            pending_id,
            error=error,
            school=school_raw,
            username=username,
            mfa=mfa,
        )

    # Credentials valid — store them and complete the OAuth flow
    cred_key = secrets.token_urlsafe(32)
    _credential_store[cred_key] = creds
    _logger.info("Smartschool login succeeded for user=%s school=%s", username, school)

    auth_code = provider.complete_authorization(
        pending_id=pending_id,
        cred_key=cred_key,
    )

    redirect_url = construct_redirect_uri(
        str(pending.params.redirect_uri),
        code=auth_code.code,
        state=pending.params.state,
    )
    return RedirectResponse(url=redirect_url, status_code=302)
