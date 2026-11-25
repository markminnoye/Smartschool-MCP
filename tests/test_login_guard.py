"""Login lockout guard: a rejected password is never posted again.

These tests drive the real ``smartschool`` login chain against a small fake
Smartschool mounted as a ``requests`` transport adapter, so no network is used
and the library's own retry logic (up to three login attempts per session) is
exercised as it runs in production.
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
import requests
from cachetools import TTLCache
from requests.adapters import BaseAdapter
from requests.models import PreparedRequest, Response
from requests.structures import CaseInsensitiveDict
from smartschool import AppCredentials, SmartSchoolAuthenticationError

import smartschool_mcp.guard as guard

HOST = "school.smartschool.be"

_LOGIN_PAGE = """
<html><body>
<form name="login_form" method="post">
  <input type="hidden" name="login_form[_token]" value="tok">
  <input type="text" name="login_form[_username]" value="">
  <input type="password" name="login_form[_password]" value="">
</form>
</body></html>
"""

_VERIFICATION_PAGE = """
<html><body>
<form name="account_verification_form" method="post">
  <input type="hidden" name="account_verification_form[_token]" value="tok">
  <input type="text" name="account_verification_form[_security_question_answer]"
         value="">
</form>
</body></html>
"""

_HOME_PAGE = "<html><body>home</body></html>"


class _Raw(io.BytesIO):
    """Just enough of urllib3's response for ``requests`` redirect handling."""

    def release_conn(self) -> None:
        pass


class FakeSmartschool(BaseAdapter):
    """A Smartschool that knows one password and one birthday answer."""

    def __init__(
        self,
        *,
        password: str = "right",
        birthday: str | None = None,
        login_host: str = HOST,
        fail_login_post: bool = False,
    ) -> None:
        super().__init__()
        self.password = password
        self.birthday = birthday
        self.login_host = login_host
        self.fail_login_post = fail_login_post
        self.logged_in = False
        self.verified = birthday is None
        self.requests: list[tuple[str, str]] = []
        self.password_posts = 0
        self.verification_posts = 0

    # -- requests adapter API -------------------------------------------------

    def send(self, request: PreparedRequest, **kwargs: object) -> Response:
        url = str(request.url)
        method = str(request.method)
        self.requests.append((method, url))
        parsed = urlparse(url)
        path = parsed.path

        if method == "POST" and path == "/login":
            if self.fail_login_post:
                raise requests.ConnectionError("network down")
            self.password_posts += 1
            form = parse_qs(str(request.body or ""))
            if form.get("login_form[_password]") == [self.password]:
                self.logged_in = True
                target = "/" if self.verified else "/account-verification"
                return self._redirect(request, target)
            return self._redirect(request, "/login?error=1")

        if method == "POST" and path == "/account-verification":
            self.verification_posts += 1
            form = parse_qs(str(request.body or ""))
            answer = form.get("account_verification_form[_security_question_answer]")
            if answer == [self.birthday]:
                self.verified = True
                return self._redirect(request, "/")
            return self._redirect(request, "/account-verification")

        if path == "/login":
            return self._page(request, _LOGIN_PAGE)
        if path == "/account-verification":
            return self._page(request, _VERIFICATION_PAGE)
        if not (self.logged_in and self.verified):
            return self._redirect(request, f"https://{self.login_host}/login")
        return self._page(request, _HOME_PAGE)

    def close(self) -> None:
        pass

    # -- helpers --------------------------------------------------------------

    @staticmethod
    def _response(request: PreparedRequest, status: int, body: str) -> Response:
        response = Response()
        response.status_code = status
        response.url = str(request.url)
        response.request = request
        response.headers = CaseInsensitiveDict({"Content-Type": "text/html"})
        response.encoding = "utf-8"
        response.raw = _Raw(body.encode())
        response._content = body.encode()
        return response

    def _page(self, request: PreparedRequest, body: str) -> Response:
        return self._response(request, 200, body)

    def _redirect(self, request: PreparedRequest, location: str) -> Response:
        response = self._response(request, 302, "")
        response.headers["Location"] = location
        return response


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Keep cookies and lockout markers out of the real ~/.cache."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(
        guard,
        "_memory_lockouts",
        TTLCache(maxsize=16, ttl=guard.MEMORY_LOCKOUT_SECONDS),
    )
    yield


def _creds(
    username: str = "john", password: str = "right", mfa: str = "2000-01-01"
) -> AppCredentials:
    return AppCredentials(username=username, password=password, main_url=HOST, mfa=mfa)


def _session(
    cls: type[guard.GuardedSession], fake: FakeSmartschool, **creds: str
) -> guard.GuardedSession:
    session = cls(_creds(**creds))
    session.mount("https://", fake)
    return session


# ---------------------------------------------------------------------------
# GuardedSession: single-user (stdio) mode, lockout kept on disk
# ---------------------------------------------------------------------------


def test_right_password_logs_in_with_one_post() -> None:
    fake = FakeSmartschool()
    session = _session(guard.GuardedSession, fake)

    response = session.get("/")

    assert response.url == f"https://{HOST}/"
    assert fake.password_posts == 1
    assert not guard.auth_failed_path(session).exists()


def test_wrong_password_is_posted_only_once() -> None:
    fake = FakeSmartschool()
    session = _session(guard.GuardedSession, fake, password="wrong")

    with pytest.raises(SmartSchoolAuthenticationError, match="not retrying"):
        session.get("/")

    assert fake.password_posts == 1
    assert guard.auth_failed_path(session).exists()


def test_after_a_rejected_password_a_new_session_sends_nothing() -> None:
    first = FakeSmartschool()
    with pytest.raises(SmartSchoolAuthenticationError):
        _session(guard.GuardedSession, first, password="wrong").get("/")

    second = FakeSmartschool()
    session = _session(guard.GuardedSession, second, password="wrong")
    with pytest.raises(SmartSchoolAuthenticationError, match="auth_failed"):
        session.get("/")

    assert second.requests == []


def test_lockout_message_names_the_file_to_delete() -> None:
    fake = FakeSmartschool()
    session = _session(guard.GuardedSession, fake, password="wrong")

    with pytest.raises(SmartSchoolAuthenticationError) as excinfo:
        session.get("/")

    assert str(guard.auth_failed_path(session)) in str(excinfo.value)


def test_wrong_birthday_is_posted_only_once() -> None:
    fake = FakeSmartschool(birthday="2000-01-01")
    session = _session(guard.GuardedSession, fake, mfa="1999-12-31")

    with pytest.raises(SmartSchoolAuthenticationError, match="not retrying"):
        session.get("/")

    assert fake.password_posts == 1
    assert fake.verification_posts == 1
    assert guard.auth_failed_path(session).exists()


def test_right_birthday_completes_the_login() -> None:
    fake = FakeSmartschool(birthday="2000-01-01")
    session = _session(guard.GuardedSession, fake)

    response = session.get("/")

    assert response.url == f"https://{HOST}/"
    assert fake.verification_posts == 1
    assert not guard.auth_failed_path(session).exists()


def test_network_error_during_login_does_not_lock_out() -> None:
    fake = FakeSmartschool(fail_login_post=True)
    session = _session(guard.GuardedSession, fake)

    with pytest.raises(requests.ConnectionError):
        session.get("/")

    assert not guard.auth_failed_path(session).exists()


def test_password_is_not_posted_to_another_host() -> None:
    fake = FakeSmartschool(login_host="evil.example")
    session = _session(guard.GuardedSession, fake)

    with pytest.raises(SmartSchoolAuthenticationError, match="not posted"):
        session.get("/")

    assert fake.password_posts == 0
    assert not any(method == "POST" for method, _ in fake.requests)
    assert not guard.auth_failed_path(session).exists()


# ---------------------------------------------------------------------------
# MemoryGuardedSession: universal (OAuth) mode, lockout in memory, expires
# ---------------------------------------------------------------------------


def test_memory_guard_blocks_without_writing_a_file() -> None:
    fake = FakeSmartschool()
    session = _session(guard.MemoryGuardedSession, fake, password="wrong")

    with pytest.raises(SmartSchoolAuthenticationError, match="minutes"):
        session.get("/")

    assert fake.password_posts == 1
    assert guard.memory_lockout_active("john", HOST)
    assert not guard.auth_failed_path(session).exists()


def test_memory_lockout_stops_the_next_login_for_that_user() -> None:
    with pytest.raises(SmartSchoolAuthenticationError):
        _session(guard.MemoryGuardedSession, FakeSmartschool(), password="wrong").get(
            "/"
        )

    second = FakeSmartschool()
    with pytest.raises(SmartSchoolAuthenticationError, match="minutes"):
        _session(guard.MemoryGuardedSession, second, password="wrong").get("/")
    assert second.requests == []


def test_memory_lockout_ignores_username_case() -> None:
    with pytest.raises(SmartSchoolAuthenticationError):
        _session(guard.MemoryGuardedSession, FakeSmartschool(), password="wrong").get(
            "/"
        )

    assert guard.memory_lockout_active("JOHN", HOST)


def test_memory_lockout_leaves_other_users_alone() -> None:
    with pytest.raises(SmartSchoolAuthenticationError):
        _session(guard.MemoryGuardedSession, FakeSmartschool(), password="wrong").get(
            "/"
        )

    fake = FakeSmartschool()
    response = _session(guard.MemoryGuardedSession, fake, username="mary").get("/")

    assert response.url == f"https://{HOST}/"
    assert fake.password_posts == 1


def test_memory_lockout_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [0.0]
    monkeypatch.setattr(
        guard,
        "_memory_lockouts",
        TTLCache(maxsize=16, ttl=guard.MEMORY_LOCKOUT_SECONDS, timer=lambda: now[0]),
    )
    with pytest.raises(SmartSchoolAuthenticationError):
        _session(guard.MemoryGuardedSession, FakeSmartschool(), password="wrong").get(
            "/"
        )
    assert guard.memory_lockout_active("john", HOST)

    now[0] += guard.MEMORY_LOCKOUT_SECONDS + 1

    assert not guard.memory_lockout_active("john", HOST)


# ---------------------------------------------------------------------------
# Logging: say where Smartschool sent us, never who or with what
# ---------------------------------------------------------------------------


def test_rejected_password_logs_the_page_without_secrets(
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake = FakeSmartschool()
    session = _session(guard.GuardedSession, fake, password="wrong")

    with (
        caplog.at_level("WARNING", logger="smartschool_mcp.guard"),
        pytest.raises(SmartSchoolAuthenticationError),
    ):
        session.get("/")

    logged = caplog.text
    assert "password" in logged
    assert "/login" in logged
    assert "error=1" not in logged
    assert "john" not in logged
    assert "wrong" not in logged


def test_rejected_birthday_logs_the_verification_page(
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake = FakeSmartschool(birthday="2000-01-01")
    session = _session(guard.GuardedSession, fake, mfa="1999-12-31")

    with (
        caplog.at_level("WARNING", logger="smartschool_mcp.guard"),
        pytest.raises(SmartSchoolAuthenticationError),
    ):
        session.get("/")

    assert "verification" in caplog.text
    assert "/account-verification" in caplog.text
    assert "1999-12-31" not in caplog.text


# ---------------------------------------------------------------------------
# After switching to a child on another school, the password stays home
# ---------------------------------------------------------------------------


def test_password_stays_on_the_home_school_after_a_child_switch() -> None:
    """A switched session whose cookie expires must not post the password at
    the other school: it belongs to the school this login is from."""
    fake = FakeSmartschool(login_host="school-b.smartschool.be")
    session = _session(guard.GuardedSession, fake)
    # What children.switch does when the child is on another school.
    session.__dict__["_home_host"] = HOST
    object.__setattr__(session.creds, "main_url", "school-b.smartschool.be")
    session.__dict__.pop("_url", None)

    with pytest.raises(SmartSchoolAuthenticationError, match="not posted"):
        session.get("/")

    assert fake.password_posts == 0
    assert not guard.auth_failed_path(session).exists()
