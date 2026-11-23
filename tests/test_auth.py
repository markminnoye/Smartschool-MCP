"""Tests for SmartschoolOAuthProvider and login form routes."""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from mcp.server.auth.provider import AuthorizationParams
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl
from smartschool import AppCredentials
from starlette.responses import Response

from smartschool_mcp.auth import (
    SmartschoolAccessToken,
    SmartschoolOAuthProvider,
    SmartschoolRefreshToken,
    _credential_store,
    _handle_login_get,
    _handle_login_post,
    _render_login_form,
    _validate_school_url,
    get_credentials,
    login_routes,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ISSUER = "http://localhost:8000"


def _make_provider() -> SmartschoolOAuthProvider:
    return SmartschoolOAuthProvider(issuer_url=ISSUER)


def _make_client(client_id: str = "test-client") -> OAuthClientInformationFull:
    return OAuthClientInformationFull(
        client_id=client_id,
        client_secret="test-secret",
        redirect_uris=[AnyUrl("http://localhost:3000/callback")],
    )


def _make_auth_params(state: str = "test-state") -> AuthorizationParams:
    return AuthorizationParams(
        state=state,
        scopes=["read"],
        code_challenge="test-challenge-abc123",
        redirect_uri=AnyUrl("http://localhost:3000/callback"),
        redirect_uri_provided_explicitly=True,
    )


# ---------------------------------------------------------------------------
# _validate_school_url
# ---------------------------------------------------------------------------


class TestValidateSchoolUrl:
    def test_valid_hostname(self) -> None:
        assert _validate_school_url("school.smartschool.be") == "school.smartschool.be"

    def test_with_scheme(self) -> None:
        assert (
            _validate_school_url("https://school.smartschool.be")
            == "school.smartschool.be"
        )

    def test_with_port(self) -> None:
        result = _validate_school_url("school.smartschool.be:8443")
        assert result == "school.smartschool.be:8443"

    def test_invalid_hostname(self) -> None:
        assert _validate_school_url("not valid!") is None

    def test_empty_string(self) -> None:
        assert _validate_school_url("") is None

    def test_single_label(self) -> None:
        assert _validate_school_url("localhost") is None

    def test_urlparse_exception_returns_none(self) -> None:
        with patch("smartschool_mcp.auth.urlparse", side_effect=ValueError("boom")):
            assert _validate_school_url("https://school.smartschool.be") is None

    def test_untrusted_suffix_rejected(self) -> None:
        with (
            patch("smartschool_mcp.auth._ALLOWED_HOSTS", set()),
            patch("smartschool_mcp.auth._ALLOWED_SUFFIXES", {".smartschool.be"}),
        ):
            assert _validate_school_url("school.example.com") is None

    def test_allowlisted_registered_domain_is_accepted(self) -> None:
        with (
            patch("smartschool_mcp.auth._ALLOWED_HOSTS", {"example.edu"}),
            patch("smartschool_mcp.auth._ALLOWED_SUFFIXES", set()),
        ):
            assert (
                _validate_school_url("school.subdomain.example.edu")
                == "school.subdomain.example.edu"
            )


# ---------------------------------------------------------------------------
# Client registration
# ---------------------------------------------------------------------------


class TestClientRegistration:
    @pytest.mark.asyncio
    async def test_register_and_get_client(self) -> None:
        provider = _make_provider()
        client = _make_client()

        await provider.register_client(client)
        result = await provider.get_client("test-client")
        assert result is not None
        assert result.client_id == "test-client"

    @pytest.mark.asyncio
    async def test_get_unknown_client_returns_none(self) -> None:
        provider = _make_provider()
        assert await provider.get_client("unknown") is None


# ---------------------------------------------------------------------------
# Authorization flow
# ---------------------------------------------------------------------------


class TestAuthorization:
    @pytest.mark.asyncio
    async def test_authorize_returns_login_url(self) -> None:
        provider = _make_provider()
        client = _make_client()
        params = _make_auth_params()

        url = await provider.authorize(client, params)
        assert url.startswith(f"{ISSUER}/smartschool-login?pending=")

    @pytest.mark.asyncio
    async def test_authorize_stores_pending_auth(self) -> None:
        provider = _make_provider()
        client = _make_client()
        params = _make_auth_params()

        url = await provider.authorize(client, params)
        pending_id = url.split("pending=")[1]

        pending = provider.get_pending_auth(pending_id)
        assert pending is not None
        assert pending.client_id == "test-client"
        assert pending.params.state == "test-state"


# ---------------------------------------------------------------------------
# Authorization code exchange
# ---------------------------------------------------------------------------


class TestAuthorizationCodeExchange:
    @pytest.mark.asyncio
    async def test_complete_authorization_creates_code(self) -> None:
        provider = _make_provider()
        client = _make_client()
        params = _make_auth_params()

        url = await provider.authorize(client, params)
        pending_id = url.split("pending=")[1]

        auth_code = provider.complete_authorization(
            pending_id=pending_id,
            cred_key="test-cred-key",
        )
        assert auth_code.code
        assert auth_code.client_id == "test-client"
        assert auth_code.cred_key == "test-cred-key"

    @pytest.mark.asyncio
    async def test_load_authorization_code(self) -> None:
        provider = _make_provider()
        client = _make_client()
        params = _make_auth_params()

        url = await provider.authorize(client, params)
        pending_id = url.split("pending=")[1]
        auth_code = provider.complete_authorization(
            pending_id=pending_id,
            cred_key="test-cred-key",
        )

        loaded = await provider.load_authorization_code(client, auth_code.code)
        assert loaded is not None
        assert loaded.code == auth_code.code

    @pytest.mark.asyncio
    async def test_load_authorization_code_wrong_client(self) -> None:
        provider = _make_provider()
        client = _make_client()
        other_client = _make_client(client_id="other-client")
        params = _make_auth_params()

        url = await provider.authorize(client, params)
        pending_id = url.split("pending=")[1]
        auth_code = provider.complete_authorization(
            pending_id=pending_id,
            cred_key="test-cred-key",
        )

        loaded = await provider.load_authorization_code(other_client, auth_code.code)
        assert loaded is None

    @pytest.mark.asyncio
    async def test_exchange_authorization_code(self) -> None:
        provider = _make_provider()
        client = _make_client()
        params = _make_auth_params()

        url = await provider.authorize(client, params)
        pending_id = url.split("pending=")[1]
        auth_code = provider.complete_authorization(
            pending_id=pending_id,
            cred_key="test-cred-key",
        )

        token = await provider.exchange_authorization_code(client, auth_code)
        assert token.access_token
        assert token.refresh_token
        assert token.token_type == "Bearer"
        assert token.expires_in == 3600

    @pytest.mark.asyncio
    async def test_exchange_removes_code(self) -> None:
        provider = _make_provider()
        client = _make_client()
        params = _make_auth_params()

        url = await provider.authorize(client, params)
        pending_id = url.split("pending=")[1]
        auth_code = provider.complete_authorization(
            pending_id=pending_id,
            cred_key="test-cred-key",
        )

        await provider.exchange_authorization_code(client, auth_code)
        # Code should be consumed (one-time use)
        loaded = await provider.load_authorization_code(client, auth_code.code)
        assert loaded is None

    @pytest.mark.asyncio
    async def test_exchange_reused_code_raises_value_error(self) -> None:
        provider = _make_provider()
        client = _make_client()
        params = _make_auth_params()

        url = await provider.authorize(client, params)
        pending_id = url.split("pending=")[1]
        auth_code = provider.complete_authorization(
            pending_id=pending_id,
            cred_key="test-cred-key",
        )

        await provider.exchange_authorization_code(client, auth_code)
        with pytest.raises(ValueError, match="already used"):
            await provider.exchange_authorization_code(client, auth_code)


# ---------------------------------------------------------------------------
# Access tokens
# ---------------------------------------------------------------------------


class TestAccessTokens:
    @pytest.mark.asyncio
    async def test_load_access_token(self) -> None:
        provider = _make_provider()
        client = _make_client()
        params = _make_auth_params()

        url = await provider.authorize(client, params)
        pending_id = url.split("pending=")[1]
        auth_code = provider.complete_authorization(
            pending_id=pending_id,
            cred_key="test-cred-key",
        )
        token = await provider.exchange_authorization_code(client, auth_code)

        access = await provider.load_access_token(token.access_token)
        assert access is not None
        assert isinstance(access, SmartschoolAccessToken)
        assert access.cred_key == "test-cred-key"

    @pytest.mark.asyncio
    async def test_load_unknown_token_returns_none(self) -> None:
        provider = _make_provider()
        assert await provider.load_access_token("nonexistent") is None

    @pytest.mark.asyncio
    async def test_expired_access_token_returns_none(self) -> None:
        provider = _make_provider()
        # Manually insert an expired token
        expired = SmartschoolAccessToken(
            token="expired-token",
            client_id="test",
            scopes=[],
            expires_at=int(time.time()) - 10,
            cred_key="key",
        )
        provider._access_tokens["expired-token"] = expired

        result = await provider.load_access_token("expired-token")
        assert result is None


# ---------------------------------------------------------------------------
# Refresh tokens
# ---------------------------------------------------------------------------


class TestRefreshTokens:
    @pytest.mark.asyncio
    async def test_refresh_token_exchange_rotates_tokens(self) -> None:
        provider = _make_provider()
        client = _make_client()
        params = _make_auth_params()

        url = await provider.authorize(client, params)
        pending_id = url.split("pending=")[1]
        auth_code = provider.complete_authorization(
            pending_id=pending_id,
            cred_key="test-cred-key",
        )
        original = await provider.exchange_authorization_code(client, auth_code)

        # Load and exchange refresh token
        rt = await provider.load_refresh_token(client, original.refresh_token)
        assert rt is not None
        assert isinstance(rt, SmartschoolRefreshToken)

        _credential_store["test-cred-key"] = AppCredentials(
            username="user",
            password="pass",
            main_url="school.smartschool.be",
            mfa="",
        )
        try:
            new_token = await provider.exchange_refresh_token(client, rt, ["read"])
            assert new_token.access_token != original.access_token
            assert new_token.refresh_token != original.refresh_token

            # Old refresh token should be gone
            old_rt = await provider.load_refresh_token(client, original.refresh_token)
            assert old_rt is None

            # New tokens carry the same cred_key
            new_access = await provider.load_access_token(new_token.access_token)
            assert new_access is not None
            assert new_access.cred_key == "test-cred-key"
        finally:
            _credential_store.pop("test-cred-key", None)

    @pytest.mark.asyncio
    async def test_load_refresh_token_wrong_client(self) -> None:
        provider = _make_provider()
        client = _make_client()
        other = _make_client(client_id="other")
        params = _make_auth_params()

        url = await provider.authorize(client, params)
        pending_id = url.split("pending=")[1]
        auth_code = provider.complete_authorization(
            pending_id=pending_id,
            cred_key="key",
        )
        token = await provider.exchange_authorization_code(client, auth_code)

        rt = await provider.load_refresh_token(other, token.refresh_token)
        assert rt is None

    @pytest.mark.asyncio
    async def test_refresh_exchange_without_credentials_raises(self) -> None:
        provider = _make_provider()
        client = _make_client()
        token = SmartschoolRefreshToken(
            token="rt-1",
            client_id="test-client",
            scopes=["read"],
            expires_at=int(time.time()) + 600,
            cred_key="missing-key",
        )
        provider._refresh_tokens[token.token] = token
        with pytest.raises(ValueError, match="credentials expired"):
            await provider.exchange_refresh_token(client, token, ["read"])


# ---------------------------------------------------------------------------
# Token revocation
# ---------------------------------------------------------------------------


class TestRevocation:
    @pytest.mark.asyncio
    async def test_revoke_access_token(self) -> None:
        provider = _make_provider()
        client = _make_client()
        params = _make_auth_params()

        url = await provider.authorize(client, params)
        pending_id = url.split("pending=")[1]
        auth_code = provider.complete_authorization(
            pending_id=pending_id,
            cred_key="key",
        )
        token = await provider.exchange_authorization_code(client, auth_code)

        access = await provider.load_access_token(token.access_token)
        assert access is not None

        await provider.revoke_token(access)
        assert await provider.load_access_token(token.access_token) is None

    @pytest.mark.asyncio
    async def test_revoke_refresh_token(self) -> None:
        provider = _make_provider()
        client = _make_client()
        params = _make_auth_params()

        url = await provider.authorize(client, params)
        pending_id = url.split("pending=")[1]
        auth_code = provider.complete_authorization(
            pending_id=pending_id,
            cred_key="key",
        )
        token = await provider.exchange_authorization_code(client, auth_code)

        rt = await provider.load_refresh_token(client, token.refresh_token)
        assert rt is not None

        await provider.revoke_token(rt)
        assert await provider.load_refresh_token(client, token.refresh_token) is None


# ---------------------------------------------------------------------------
# Credential store
# ---------------------------------------------------------------------------


class TestCredentialStore:
    def test_get_credentials(self) -> None:
        creds = AppCredentials(
            username="user", password="pass", main_url="school.be", mfa=""
        )
        _credential_store["test-key"] = creds
        try:
            result = get_credentials("test-key")
            assert result is not None
            assert result.username == "user"
        finally:
            _credential_store.pop("test-key", None)

    def test_get_credentials_missing(self) -> None:
        assert get_credentials("nonexistent") is None


# ---------------------------------------------------------------------------
# Login form and route handlers
# ---------------------------------------------------------------------------


class TestLoginHandlers:
    def test_render_login_form_includes_error_and_values(self) -> None:
        response = _render_login_form(
            "pending-123",
            error="Bad credentials",
            school="school.smartschool.be",
            username="john",
            mfa="2000-01-01",
        )
        body = response.body.decode()
        assert response.status_code == 200
        assert "Bad credentials" in body
        assert 'name="pending" value="pending-123"' in body
        assert 'value="school.smartschool.be"' in body
        assert 'value="john"' in body
        assert 'value="2000-01-01"' in body

    def test_render_login_form_escapes_user_values(self) -> None:
        response = _render_login_form(
            '<pending">',
            error="<b>bad</b>",
            school='"><script>alert(1)</script>',
            username='"><img src=x onerror=alert(1)>',
            mfa="<svg/onload=alert(1)>",
        )
        body = response.body.decode()
        assert "<script>alert(1)</script>" not in body
        assert "<img src=x onerror=alert(1)>" not in body
        assert "<pending>" not in body
        assert "&lt;b&gt;bad&lt;/b&gt;" in body
        assert 'name="pending" value="' in body

    @pytest.mark.asyncio
    async def test_login_routes_dispatches_get(self) -> None:
        provider = MagicMock()
        route = login_routes(provider)[0]
        request = MagicMock()
        request.method = "GET"

        with (
            patch(
                "smartschool_mcp.auth._handle_login_get",
                new=AsyncMock(return_value=Response("ok")),
            ) as get_mock,
            patch(
                "smartschool_mcp.auth._handle_login_post",
                new=AsyncMock(return_value=Response("nope")),
            ) as post_mock,
        ):
            await route.endpoint(request)

        get_mock.assert_awaited_once_with(request, provider)
        post_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_login_routes_dispatches_post(self) -> None:
        provider = MagicMock()
        route = login_routes(provider)[0]
        request = MagicMock()
        request.method = "POST"

        with (
            patch(
                "smartschool_mcp.auth._handle_login_get",
                new=AsyncMock(return_value=Response("nope")),
            ) as get_mock,
            patch(
                "smartschool_mcp.auth._handle_login_post",
                new=AsyncMock(return_value=Response("ok")),
            ) as post_mock,
        ):
            await route.endpoint(request)

        post_mock.assert_awaited_once_with(request, provider)
        get_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_handle_login_get_invalid_pending_returns_400(self) -> None:
        provider = MagicMock()
        provider.get_pending_auth.return_value = None
        request = MagicMock()
        request.query_params = {"pending": "missing"}

        response = await _handle_login_get(request, provider)
        assert response.status_code == 400
        assert "Invalid or expired login link" in response.body.decode()

    @pytest.mark.asyncio
    async def test_handle_login_get_valid_pending_returns_form(self) -> None:
        provider = MagicMock()
        provider.get_pending_auth.return_value = object()
        request = MagicMock()
        request.query_params = {"pending": "abc123"}

        response = await _handle_login_get(request, provider)
        assert response.status_code == 200
        assert 'name="pending" value="abc123"' in response.body.decode()

    @pytest.mark.asyncio
    async def test_handle_login_post_expired_pending_returns_400(self) -> None:
        provider = MagicMock()
        provider.get_pending_auth.return_value = None
        request = MagicMock()
        request.form = AsyncMock(
            return_value={
                "pending": "expired",
                "school": "school.smartschool.be",
                "username": "john",
                "password": "secret",
                "mfa": "",
            }
        )

        response = await _handle_login_post(request, provider)
        assert response.status_code == 400
        assert "Login session expired" in response.body.decode()

    @pytest.mark.asyncio
    async def test_handle_login_post_invalid_school_rerenders_form(self) -> None:
        provider = MagicMock()
        provider.get_pending_auth.return_value = object()
        request = MagicMock()
        request.form = AsyncMock(
            return_value={
                "pending": "abc",
                "school": "not valid!",
                "username": "john",
                "password": "secret",
                "mfa": "",
            }
        )

        response = await _handle_login_post(request, provider)
        body = response.body.decode()
        assert response.status_code == 200
        assert "Invalid school URL." in body
        assert 'value="not valid!"' in body

    @pytest.mark.asyncio
    async def test_handle_login_post_requires_username_and_password(self) -> None:
        provider = MagicMock()
        provider.get_pending_auth.return_value = object()
        request = MagicMock()
        request.form = AsyncMock(
            return_value={
                "pending": "abc",
                "school": "school.smartschool.be",
                "username": "",
                "password": "",
                "mfa": "",
            }
        )

        response = await _handle_login_post(request, provider)
        assert response.status_code == 200
        assert "Username and password are required." in response.body.decode()

    @pytest.mark.asyncio
    async def test_handle_login_post_failed_smartschool_login(self) -> None:
        provider = MagicMock()
        provider.get_pending_auth.return_value = object()
        request = MagicMock()
        request.form = AsyncMock(
            return_value={
                "pending": "abc",
                "school": "school.smartschool.be",
                "username": "john",
                "password": "wrong",
                "mfa": "",
            }
        )

        with patch(
            "smartschool_mcp.auth.MemoryGuardedSession",
            side_effect=RuntimeError("bad"),
        ):
            response = await _handle_login_post(request, provider)

        assert response.status_code == 200
        assert "Login failed" in response.body.decode()

    @pytest.mark.asyncio
    async def test_handle_login_post_success_redirects(self) -> None:
        provider = MagicMock()
        params = SimpleNamespace(
            redirect_uri=AnyUrl("http://localhost:3000/callback"),
            state="state123",
        )
        provider.get_pending_auth.return_value = SimpleNamespace(params=params)
        provider.complete_authorization.return_value = SimpleNamespace(code="code-123")

        request = MagicMock()
        request.form = AsyncMock(
            return_value={
                "pending": "abc",
                "school": "school.smartschool.be",
                "username": "john",
                "password": "secret",
                "mfa": "2000-01-01",
            }
        )

        mock_session = MagicMock()
        mock_session.get.return_value = object()

        with patch(
            "smartschool_mcp.auth.MemoryGuardedSession", return_value=mock_session
        ):
            response = await _handle_login_post(request, provider)

        assert response.status_code == 302
        assert "code=code-123" in response.headers["location"]
        assert "state=state123" in response.headers["location"]

    @staticmethod
    def _login_request(password: str = "secret") -> MagicMock:
        request = MagicMock()
        request.form = AsyncMock(
            return_value={
                "pending": "abc",
                "school": "school.smartschool.be",
                "username": "john",
                "password": password,
                "mfa": "2000-01-01",
            }
        )
        return request

    @pytest.mark.asyncio
    async def test_handle_login_post_stops_during_memory_lockout(self) -> None:
        provider = MagicMock()
        provider.get_pending_auth.return_value = object()

        with (
            patch("smartschool_mcp.auth.memory_lockout_active", return_value=True),
            patch("smartschool_mcp.auth.MemoryGuardedSession") as mock_session,
        ):
            response = await _handle_login_post(self._login_request(), provider)

        mock_session.assert_not_called()
        body = response.body.decode()
        assert response.status_code == 200
        assert "15 minuten" in body
        assert ".cache" not in body

    @pytest.mark.asyncio
    async def test_handle_login_post_shows_lockout_from_guard(self) -> None:
        provider = MagicMock()
        provider.get_pending_auth.return_value = object()

        with (
            patch(
                "smartschool_mcp.auth.memory_lockout_active",
                side_effect=[False, True],
            ),
            patch(
                "smartschool_mcp.auth.MemoryGuardedSession",
                side_effect=RuntimeError("LOGIN FAILED"),
            ),
        ):
            response = await _handle_login_post(self._login_request("wrong"), provider)

        body = response.body.decode()
        assert response.status_code == 200
        assert "15 minuten" in body
        assert "Login failed — check" not in body

    @pytest.mark.asyncio
    async def test_handle_login_post_network_error_is_not_a_bad_password(
        self,
    ) -> None:
        import requests

        provider = MagicMock()
        provider.get_pending_auth.return_value = object()

        with patch(
            "smartschool_mcp.auth.MemoryGuardedSession",
            side_effect=requests.ConnectionError("down"),
        ):
            response = await _handle_login_post(self._login_request(), provider)

        body = response.body.decode()
        assert "Could not reach Smartschool" in body
        assert "check your school URL" not in body


def test_credential_store_outlives_an_idle_hour() -> None:
    import smartschool_mcp.auth as auth

    assert auth._credential_store.ttl == auth._REFRESH_TOKEN_TTL


@pytest.mark.asyncio
async def test_revoking_refresh_token_forgets_the_password() -> None:
    import smartschool_mcp.auth as auth

    provider = auth.SmartschoolOAuthProvider("https://mcp.example.com")
    creds = AppCredentials(
        username="john", password="pw", main_url="school.smartschool.be", mfa=""
    )
    refresh = auth.SmartschoolRefreshToken(
        token="r1", client_id="c", scopes=[], expires_at=None, cred_key="k-revoke"
    )
    provider._refresh_tokens["r1"] = refresh
    with patch.dict(auth._credential_store, {"k-revoke": creds}):
        await provider.revoke_token(refresh)
        assert "k-revoke" not in auth._credential_store
    assert "r1" not in provider._refresh_tokens
