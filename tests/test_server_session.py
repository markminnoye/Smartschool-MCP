"""Tests for server session selection and caching helpers."""

from __future__ import annotations

import importlib
from builtins import __import__ as builtin_import
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from cachetools import TTLCache
from smartschool import AppCredentials


def _reload_server_module():
    import smartschool_mcp.server as srv

    return importlib.reload(srv)


def _live_response(url: str = "https://school.smartschool.be/") -> MagicMock:
    """A response object that looks like a successfully authenticated GET /."""
    return MagicMock(url=url)


def _live_session(url: str = "https://school.smartschool.be/") -> MagicMock:
    """A session whose liveness probe succeeds."""
    session = MagicMock()
    session.get.return_value = _live_response(url)
    # MagicMock auto-creates attributes, so the probe marker must start unset.
    session._mcp_liveness_probe = None
    return session


def test_env_session_is_ttl_cached() -> None:
    srv = _reload_server_module()
    srv._env_session.cache_clear()

    with (
        patch("smartschool_mcp.server.env_credentials", return_value="env-creds"),
        patch(
            "smartschool_mcp.server.GuardedSession", return_value="session"
        ) as mock_ss,
    ):
        first = srv._env_session()
        second = srv._env_session()

    assert first == "session"
    assert second == "session"
    mock_ss.assert_called_once_with("env-creds")


def test_env_session_cache_expires_so_stdio_sessions_are_rebuilt() -> None:
    """A long-lived stdio process must not reuse one session object forever."""
    srv = _reload_server_module()

    assert isinstance(srv._env_session_cache, TTLCache)
    assert srv._env_session_cache.maxsize == 1
    assert srv._env_session_cache.ttl == srv._SESSION_TTL_SECONDS

    # Expiry must actually rebuild the session, not hand back the stale one.
    srv._env_session_cache.clear()
    with (
        patch("smartschool_mcp.server.env_credentials", return_value="env-creds"),
        patch(
            "smartschool_mcp.server.GuardedSession", side_effect=["first", "second"]
        ) as mock_ss,
    ):
        assert srv._env_session() == "first"
        srv._env_session_cache.clear()  # stand in for the TTL elapsing
        assert srv._env_session() == "second"

    assert mock_ss.call_count == 2


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://school.smartschool.be/", False),
        ("https://school.smartschool.be/index.php", False),
        ("https://school.smartschool.be/login", True),
        ("https://school.smartschool.be/account-verification", True),
        ("https://school.smartschool.be/2fa", True),
        ("https://school.smartschool.be/login/", True),
        # "login" must match a whole path segment, not a substring.
        ("https://school.smartschool.be/logins-overview", False),
    ],
)
def test_is_auth_url(url: str, expected: bool) -> None:
    srv = _reload_server_module()

    assert srv._is_auth_url(url) is expected


def test_ensure_live_session_returns_live_session() -> None:
    srv = _reload_server_module()
    session = _live_session()

    assert srv._ensure_live_session(session) is session
    session.get.assert_called_once_with("/")


def test_ensure_live_session_probes_only_once_per_session() -> None:
    """The probe costs an HTTP request, so it must not run per tool call."""
    srv = _reload_server_module()
    session = _live_session()

    srv._ensure_live_session(session)
    srv._ensure_live_session(session)

    session.get.assert_called_once_with("/")


def test_ensure_live_session_raises_when_redirected_to_login() -> None:
    """A dead cookie that cannot be renewed must fail loudly, not silently."""
    srv = _reload_server_module()
    session = _live_session("https://school.smartschool.be/login")

    with pytest.raises(srv.AuthenticationError, match="not authenticated"):
        srv._ensure_live_session(session)


def test_ensure_live_session_remembers_failure_without_retrying() -> None:
    """Repeated logins risk locking the real account, so failures are cached."""
    srv = _reload_server_module()
    session = MagicMock()
    session._mcp_liveness_probe = None
    session.get.side_effect = RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        srv._ensure_live_session(session)
    with pytest.raises(RuntimeError, match="boom"):
        srv._ensure_live_session(session)

    session.get.assert_called_once_with("/")


def test_ensure_live_session_tolerates_sessions_that_reject_attributes() -> None:
    """A session that cannot hold the marker still works, just unmemoised."""
    srv = _reload_server_module()

    class Slotted:
        __slots__ = ()

        def get(self, _url: str) -> MagicMock:
            return _live_response()

    session = Slotted()

    assert srv._ensure_live_session(session) is session


def test_session_runs_liveness_probe_on_env_session() -> None:
    srv = _reload_server_module()

    with (
        patch.object(srv, "_env_session", return_value="env-session"),
        patch.object(
            srv, "_ensure_live_session", side_effect=lambda s: f"live:{s}"
        ) as mock_probe,
    ):
        result = srv._session()

    assert result == "live:env-session"
    mock_probe.assert_called_once_with("env-session")


def test_cached_app_session_uses_credentials_cache() -> None:
    srv = _reload_server_module()
    srv._session_cache.clear()

    with (
        patch(
            "smartschool_mcp.server.AppCredentials",
            side_effect=lambda **kwargs: kwargs,
        ) as mock_creds,
        patch(
            "smartschool_mcp.server.MemoryGuardedSession", return_value="app-session"
        ) as mock_ss,
    ):
        first = srv._cached_app_session("user", "pass", "school.smartschool.be", "")
        second = srv._cached_app_session("user", "pass", "school.smartschool.be", "")

    assert first == "app-session"
    assert second == "app-session"
    mock_creds.assert_called_once_with(
        username="user",
        password="pass",
        main_url="school.smartschool.be",
        mfa="",
    )
    mock_ss.assert_called_once()


def test_session_uses_oauth_credentials_when_available() -> None:
    srv = _reload_server_module()
    creds = AppCredentials(
        username="john",
        password="secret",
        main_url="school.smartschool.be",
        mfa="2000-01-01",
    )
    token = SimpleNamespace(cred_key="cred-key")

    with (
        patch(
            "mcp.server.auth.middleware.auth_context.get_access_token",
            return_value=token,
        ),
        patch("smartschool_mcp.auth.get_credentials", return_value=creds),
        patch.object(
            srv, "_cached_app_session", return_value="oauth-session"
        ) as mock_cached,
        patch.object(srv, "_env_session", return_value="env-session") as mock_env,
        patch.object(srv, "_ensure_live_session", side_effect=lambda s: s),
    ):
        result = srv._session()

    assert result == "oauth-session"
    mock_cached.assert_called_once_with(
        "john",
        "secret",
        "school.smartschool.be",
        "2000-01-01",
    )
    mock_env.assert_not_called()


def test_session_falls_back_to_env_when_cred_key_missing() -> None:
    srv = _reload_server_module()

    with (
        patch(
            "mcp.server.auth.middleware.auth_context.get_access_token",
            return_value=SimpleNamespace(),
        ),
        patch.object(
            srv, "_cached_app_session", return_value="oauth-session"
        ) as mock_cached,
        patch.object(srv, "_env_session", return_value="env-session") as mock_env,
        patch.object(srv, "_ensure_live_session", side_effect=lambda s: s),
    ):
        result = srv._session()

    assert result == "env-session"
    mock_cached.assert_not_called()
    mock_env.assert_called_once()


def test_session_falls_back_to_env_when_import_error() -> None:
    srv = _reload_server_module()

    def guarded_import(name, *args, **kwargs):
        if name == "mcp.server.auth.middleware.auth_context":
            raise ImportError("missing auth context")
        return builtin_import(name, *args, **kwargs)

    with (
        patch("builtins.__import__", side_effect=guarded_import),
        patch.object(srv, "_env_session", return_value="env-session") as mock_env,
        patch.object(srv, "_ensure_live_session", side_effect=lambda s: s),
    ):
        result = srv._session()

    assert result == "env-session"
    mock_env.assert_called_once()


def test_session_raises_auth_error_when_credentials_not_found() -> None:
    srv = _reload_server_module()
    token = SimpleNamespace(cred_key="missing-key")

    with (
        pytest.raises(srv.AuthenticationError, match="re-authentication required"),
        patch(
            "mcp.server.auth.middleware.auth_context.get_access_token",
            return_value=token,
        ),
        patch("smartschool_mcp.auth.get_credentials", return_value=None),
        patch.object(
            srv, "_cached_app_session", return_value="oauth-session"
        ) as mock_cached,
        patch.object(srv, "_env_session", return_value="env-session") as mock_env,
    ):
        srv._session()

    mock_cached.assert_not_called()
    mock_env.assert_not_called()


def test_env_session_is_guarded_against_lockout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    srv = _reload_server_module()
    srv._env_session.cache_clear()

    with patch("smartschool_mcp.server.env_credentials", return_value=None):
        session = srv._env_session()

    from smartschool_mcp.guard import GuardedSession, MemoryGuardedSession

    assert isinstance(session, GuardedSession)
    assert not isinstance(session, MemoryGuardedSession)


def test_app_session_keeps_its_lockout_in_memory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    srv = _reload_server_module()
    srv._session_cache.clear()

    session = srv._cached_app_session(
        "user", "pass", "school.smartschool.be", "2000-01-01"
    )

    from smartschool_mcp.guard import MemoryGuardedSession

    assert isinstance(session, MemoryGuardedSession)


def test_bad_settings_fail_before_any_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A typo in the birth date must not cost a Smartschool login attempt."""
    srv = _reload_server_module()
    srv._env_session.cache_clear()
    monkeypatch.setenv("SMARTSCHOOL_USERNAME", "john.doe")
    monkeypatch.setenv("SMARTSCHOOL_PASSWORD", "secret")
    monkeypatch.setenv("SMARTSCHOOL_MAIN_URL", "myschool.smartschool.be")
    monkeypatch.setenv("SMARTSCHOOL_MFA", "31/02/2012")

    with (
        patch("smartschool_mcp.server.Smartschool") as mock_ss,
        pytest.raises(ValueError, match="SMARTSCHOOL_MFA"),
    ):
        srv._env_session()

    mock_ss.assert_not_called()
