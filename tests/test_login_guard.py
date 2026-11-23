"""Login lockout for the MCP server. No network."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from smartschool import Smartschool, SmartSchoolAuthenticationError

import smartschool_mcp.guard as guard
import smartschool_mcp.server as server


def test_normalize_school_subdomain() -> None:
    assert guard.normalize_school_main_url("dering") == "dering.smartschool.be"
    assert guard.normalize_school_main_url("  DERING  ") == "dering.smartschool.be"
    assert (
        guard.normalize_school_main_url("https://dering.smartschool.be")
        == "dering.smartschool.be"
    )
    assert (
        guard.normalize_school_main_url("https://dering.smartschool.be/planner")
        == "dering.smartschool.be"
    )
    assert (
        guard.normalize_school_main_url("HTTPS://Dering.Smartschool.BE/login")
        == "dering.smartschool.be"
    )
    assert (
        guard.normalize_school_main_url("dering.smartschool.be")
        == "dering.smartschool.be"
    )
    assert (
        guard.normalize_school_main_url("school.smartschool.be:8443")
        == "school.smartschool.be:8443"
    )
    assert guard.normalize_school_main_url("") == ""
    assert guard.normalize_school_main_url("school.example.com") == "school.example.com"


def test_normalize_school_subdomain_rejects_blank_host() -> None:
    with pytest.raises(ValueError, match="subdomain"):
        guard.normalize_school_main_url("https://")
    with pytest.raises(ValueError, match="subdomain"):
        guard.normalize_school_main_url(".smartschool.be")
    with pytest.raises(ValueError, match="subdomain"):
        guard.normalize_school_main_url("not valid!")


def test_wrong_password_posts_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    posts: list[str] = []
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    class _Resp:
        url = "https://school.smartschool.be/login"

    def _super_login(self, response):
        posts.append(response.url)
        return _Resp()

    monkeypatch.setattr(Smartschool, "_do_login", _super_login)
    session = _guard(tmp_path)
    with pytest.raises(SmartSchoolAuthenticationError, match="niet opnieuw proberen"):
        session._do_login(_Resp())
    assert posts == ["https://school.smartschool.be/login"]
    assert (
        tmp_path / ".cache" / "smartschool" / "school" / "child" / "auth_failed"
    ).exists()
    with pytest.raises(SmartSchoolAuthenticationError, match="niet opnieuw proberen"):
        session._do_login(_Resp())
    assert len(posts) == 1


def test_login_query_error_posts_once_and_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    posts: list[str] = []
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    def _super_login(self, response):
        posts.append(response.url)
        return SimpleNamespace(url="https://school.smartschool.be/login?error=1")

    monkeypatch.setattr(Smartschool, "_do_login", _super_login)
    session = _guard(tmp_path)
    with pytest.raises(SmartSchoolAuthenticationError, match="niet opnieuw proberen"):
        session._do_login(SimpleNamespace(url="https://school.smartschool.be/login"))
    assert posts == ["https://school.smartschool.be/login"]
    assert (
        tmp_path / ".cache" / "smartschool" / "school" / "child" / "auth_failed"
    ).exists()
    with pytest.raises(SmartSchoolAuthenticationError, match="niet opnieuw proberen"):
        session._do_login(SimpleNamespace(url="https://school.smartschool.be/login"))
    assert len(posts) == 1


def test_wrong_host_does_not_post(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    called: list[str] = []

    def _super_login(self, response):
        called.append("posted")
        return response

    monkeypatch.setattr(Smartschool, "_do_login", _super_login)
    session = _guard(tmp_path)
    with pytest.raises(SmartSchoolAuthenticationError, match="not posted"):
        session._do_login(SimpleNamespace(url="https://evil.example/login"))
    assert called == []
    assert not (
        tmp_path / ".cache" / "smartschool" / "school" / "child" / "auth_failed"
    ).exists()


def test_verification_that_stays_on_account_page_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    def _super_verify(self, response):
        return SimpleNamespace(url="https://school.smartschool.be/account-verification")

    monkeypatch.setattr(Smartschool, "_do_login_verification", _super_verify)
    session = _guard(tmp_path)
    with pytest.raises(SmartSchoolAuthenticationError, match="niet opnieuw proberen"):
        session._do_login_verification(
            SimpleNamespace(url="https://school.smartschool.be/account-verification")
        )
    assert (
        tmp_path / ".cache" / "smartschool" / "school" / "child" / "auth_failed"
    ).exists()


def test_password_post_may_continue_to_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    def _super_login(self, response):
        return SimpleNamespace(url="https://school.smartschool.be/account-verification")

    monkeypatch.setattr(Smartschool, "_do_login", _super_login)
    session = _guard(tmp_path)
    posted = session._do_login(
        SimpleNamespace(url="https://school.smartschool.be/login")
    )
    assert str(posted.url).endswith("/account-verification")
    assert not (
        tmp_path / ".cache" / "smartschool" / "school" / "child" / "auth_failed"
    ).exists()


def test_request_refuses_existing_auth_failed_without_http(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    failed = tmp_path / ".cache" / "smartschool" / "school" / "child" / "auth_failed"
    failed.parent.mkdir(parents=True)
    failed.write_text("login failed\n", encoding="utf-8")
    called: list[str] = []

    def _super_request(self, method, url, **kwargs):
        called.append(url)
        return None

    monkeypatch.setattr(Smartschool, "request", _super_request)
    session = _guard(tmp_path)
    with pytest.raises(SmartSchoolAuthenticationError, match="auth_failed"):
        session.request("GET", "/course-list/api/v1/courses")
    assert called == []


def test_open_env_session_normalizes_subdomain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("SMARTSCHOOL_USERNAME", "child")
    monkeypatch.setenv("SMARTSCHOOL_PASSWORD", "secret")
    monkeypatch.setenv("SMARTSCHOOL_MFA", "2014-01-02")
    monkeypatch.setenv("SMARTSCHOOL_MAIN_URL", "https://dering.smartschool.be/login")
    captured: dict[str, str] = {}

    def _capture(creds):
        captured["main_url"] = creds.main_url
        return "session"

    monkeypatch.setattr(server, "GuardedSession", _capture)
    assert server._open_env_session() == "session"
    assert captured["main_url"] == "dering.smartschool.be"


def test_open_env_session_accepts_bare_subdomain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("SMARTSCHOOL_USERNAME", "child")
    monkeypatch.setenv("SMARTSCHOOL_PASSWORD", "secret")
    monkeypatch.setenv("SMARTSCHOOL_MFA", "2014-01-02")
    monkeypatch.setenv("SMARTSCHOOL_MAIN_URL", "dering")
    captured: dict[str, str] = {}

    def _capture(creds):
        captured["main_url"] = creds.main_url
        return "session"

    monkeypatch.setattr(server, "GuardedSession", _capture)
    server._open_env_session()
    assert captured["main_url"] == "dering.smartschool.be"


def test_open_env_session_blocked_by_auth_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("SMARTSCHOOL_USERNAME", "child")
    monkeypatch.setenv("SMARTSCHOOL_PASSWORD", "secret")
    monkeypatch.setenv("SMARTSCHOOL_MFA", "2014-01-02")
    monkeypatch.setenv("SMARTSCHOOL_MAIN_URL", "dering.smartschool.be")
    failed = tmp_path / ".cache" / "smartschool" / "dering" / "child" / "auth_failed"
    failed.parent.mkdir(parents=True)
    failed.write_text("login failed\n", encoding="utf-8")

    def _boom(_creds):
        raise AssertionError("session created")

    monkeypatch.setattr(server, "GuardedSession", _boom)
    with pytest.raises(SmartSchoolAuthenticationError, match="auth_failed"):
        server._open_env_session()


def test_cached_app_session_blocked_by_memory_lockout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server._session_cache.clear()
    monkeypatch.setattr(guard, "_memory_lockouts", guard.TTLCache(16, 60))
    guard._memory_lockouts[guard._memory_key("child", "school.smartschool.be")] = True

    def _boom(_creds):
        raise AssertionError("session created")

    monkeypatch.setattr(server, "MemoryGuardedSession", _boom)
    with pytest.raises(SmartSchoolAuthenticationError, match="15 minuten"):
        server._cached_app_session(
            "child", "secret", "school.smartschool.be", "2014-01-02"
        )


def test_cached_app_session_ignores_the_file_lockout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Universal mode: a stale auth_failed file from the public form is ignored."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(guard, "_memory_lockouts", guard.TTLCache(16, 60))
    server._session_cache.clear()
    failed = tmp_path / ".cache" / "smartschool" / "school" / "child" / "auth_failed"
    failed.parent.mkdir(parents=True)
    failed.write_text("login failed\n", encoding="utf-8")

    session = server._cached_app_session(
        "child", "secret", "school.smartschool.be", "2014-01-02"
    )

    assert isinstance(session, guard.MemoryGuardedSession)
    server._session_cache.clear()


def test_server_instructions_name_the_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    for key in (
        "SMARTSCHOOL_CHILD",
        "SMARTSCHOOL_CHILD_NAME",
        "SMARTSCHOOL_PROFILE",
        "GROK_PLUGIN_DATA",
    ):
        monkeypatch.delenv(key, raising=False)
    assert server._server_instructions() is None
    monkeypatch.setenv("SMARTSCHOOL_CHILD_NAME", "  Emma\nJansen ")
    text = server._server_instructions()
    assert text is not None
    assert "Emma Jansen" in text
    assert "get_children" in text
    assert "switch_child" in text
    assert "auth_failed" in text


def test_server_instructions_use_the_saved_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("smartschool_mcp.credentials.keychain_enabled", lambda: False)
    for key in (
        "SMARTSCHOOL_CHILD",
        "SMARTSCHOOL_CHILD_NAME",
        "SMARTSCHOOL_USERNAME",
        "SMARTSCHOOL_USER",
        "SMARTSCHOOL_PASSWORD",
        "SMARTSCHOOL_PROFILE",
        "GROK_PLUGIN_DATA",
    ):
        monkeypatch.delenv(key, raising=False)
    from smartschool_mcp.credentials import save_credentials

    save_credentials("student", "pw-secret", "depass", "2014-01-02", "Elliot")
    text = server._server_instructions()
    assert text is not None
    assert "Elliot" in text
    monkeypatch.setenv("SMARTSCHOOL_CHILD", "Emma")
    named = server._server_instructions()
    assert named is not None
    assert "Emma" in named
    assert "Elliot" not in named


def _guard(tmp_path: Path) -> guard.GuardedSession:
    creds = SimpleNamespace(
        username="child",
        password="wrong",
        main_url="school.smartschool.be",
        mfa="2014-01-02",
    )

    def _validate(self) -> None:
        return None

    creds.validate = _validate.__get__(creds)
    assert tmp_path.exists()
    return guard.GuardedSession(creds)


def test_network_error_during_login_does_not_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import requests

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    calls: list[str] = []

    def _super_login(self, response):
        calls.append(response.url)
        if len(calls) == 1:
            raise requests.ConnectionError("wifi gone")
        return SimpleNamespace(url="https://school.smartschool.be/account-verification")

    monkeypatch.setattr(Smartschool, "_do_login", _super_login)
    session = _guard(tmp_path)
    login_page = SimpleNamespace(url="https://school.smartschool.be/login")

    with pytest.raises(requests.ConnectionError):
        session._do_login(login_page)
    assert not (
        tmp_path / ".cache" / "smartschool" / "school" / "child" / "auth_failed"
    ).exists()

    # The next try posts again and gets through.
    posted = session._do_login(login_page)
    assert posted.url.endswith("/account-verification")
    assert len(calls) == 2


def _memory_guard(username: str = "child") -> guard.MemoryGuardedSession:
    creds = SimpleNamespace(
        username=username,
        password="wrong",
        main_url="school.smartschool.be",
        mfa="2014-01-02",
    )
    creds.validate = (lambda self: None).__get__(creds)
    return guard.MemoryGuardedSession(creds)


def test_memory_guard_blocks_in_memory_without_a_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(guard, "_memory_lockouts", guard.TTLCache(16, 60))
    posts: list[str] = []

    def _super_login(self, response):
        posts.append(response.url)
        return SimpleNamespace(url="https://school.smartschool.be/login?error=1")

    monkeypatch.setattr(Smartschool, "_do_login", _super_login)
    login_page = SimpleNamespace(url="https://school.smartschool.be/login")

    with pytest.raises(SmartSchoolAuthenticationError, match="15 minuten"):
        _memory_guard()._do_login(login_page)
    assert guard.memory_lockout_active("CHILD", "school.smartschool.be")
    assert not list(tmp_path.rglob("auth_failed"))

    # A fresh session for the same login is refused before posting again.
    with pytest.raises(SmartSchoolAuthenticationError, match="15 minuten"):
        _memory_guard()._do_login(login_page)
    assert len(posts) == 1
    # Another user is not affected.
    assert not guard.memory_lockout_active("other", "school.smartschool.be")


def test_memory_lockout_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [1000.0]
    cache = guard.TTLCache(16, 60, timer=lambda: now[0])
    monkeypatch.setattr(guard, "_memory_lockouts", cache)
    cache[guard._memory_key("child", "school.smartschool.be")] = True
    assert guard.memory_lockout_active("child", "school.smartschool.be")
    now[0] += 61
    assert not guard.memory_lockout_active("child", "school.smartschool.be")


@pytest.mark.parametrize("username", ["../../etc", "a/b", "..", "x\\y"])
def test_cache_dir_fallback_keeps_username_in_one_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, username: str
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    root = tmp_path / ".cache" / "smartschool"
    path = guard.cache_dir(username, "school.example.com")
    assert path.parent == root / "school.example.com"
    assert path.resolve().is_relative_to(root.resolve())
