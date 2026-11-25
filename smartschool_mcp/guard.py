"""Login lockout guard: a password Smartschool rejected is never posted again.

The ``smartschool`` library retries a failed login up to three times per
session, and this server builds a new session every ``SESSION_TTL_SECONDS``.
With a wrong password that adds up to many failed logins, and Smartschool
locks the account after a few of them.

``GuardedSession`` (single-user / stdio mode) posts a password once. When
Smartschool answers with the login page again, it writes an ``auth_failed``
marker next to the session's cookie cache and refuses every later request
until a person deletes that file.

``MemoryGuardedSession`` (universal / OAuth mode) keeps the lockout in memory
and lets it expire after ``MEMORY_LOCKOUT_SECONDS``. Its login form is public:
a file marker would let anyone lock a user out for good by typing their
username with a wrong password once.

Both pin the credential POSTs to the school's own host over https, so a
redirect elsewhere never receives the password.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import NoReturn
from urllib.parse import urlparse

import requests
from cachetools import TTLCache
from smartschool import Credentials, Smartschool, SmartSchoolAuthenticationError

MEMORY_LOCKOUT_SECONDS = 15 * 60

_logger = logging.getLogger(__name__)

# One credential POST at a time in this process, so two tool calls racing on
# an expired cookie cannot both send a password that turns out to be wrong.
_LOGIN_LOCK = threading.Lock()

_memory_lockouts: TTLCache[tuple[str, str], bool] = TTLCache(
    maxsize=4096, ttl=MEMORY_LOCKOUT_SECONDS
)
_memory_lockouts_lock = threading.Lock()


def _school_host(main_url: str) -> str:
    """Hostname of a ``main_url`` given as ``host``, ``host:port`` or a URL."""
    raw = main_url.strip()
    if "://" in raw:
        return (urlparse(raw).hostname or "").lower()
    return raw.split("/")[0].split(":")[0].lower()


def _path_has(url: str, *segments: str) -> bool:
    parts = set(urlparse(url).path.split("/"))
    return any(segment in parts for segment in segments)


def auth_failed_path(session: Smartschool) -> Path:
    """The lockout marker of *session*, next to its cookie cache."""
    return session.cache_path / "auth_failed"


def _memory_key(username: str, main_url: str) -> tuple[str, str]:
    return username.strip().lower(), _school_host(main_url)


def memory_lockout_active(username: str, main_url: str) -> bool:
    """True while a rejected login for this user and school is remembered."""
    with _memory_lockouts_lock:
        return _memory_key(username, main_url) in _memory_lockouts


def memory_lockout_message() -> str:
    minutes = MEMORY_LOCKOUT_SECONDS // 60
    return (
        "Smartschool rejected the login; not retrying for "
        f"{minutes} minutes, to avoid locking the account."
    )


class GuardedSession(Smartschool):
    """A ``Smartschool`` session that posts a rejected password only once."""

    def request(self, method, url, **kwargs):  # type: ignore[no-untyped-def]
        self._refuse_if_blocked()
        return super().request(method, url, **kwargs)

    def _do_login(self, response: requests.Response) -> requests.Response:
        self._assert_credential_target(str(response.url))
        with _LOGIN_LOCK:
            # Another session may have been refused while this one waited.
            self._refuse_if_blocked()
            # A timeout, DNS failure or 5xx says nothing about the password,
            # so a RequestException goes up without locking anyone out.
            # _do_login is private library API, absent from its type stubs.
            posted: requests.Response = super()._do_login(response)  # type: ignore[misc]
            # Staying on /login (including /login?error=1) means the password
            # was rejected; account verification or 2FA come next otherwise.
            if _path_has(str(posted.url), "login"):
                self._block("password", str(posted.url))
        return posted

    def _do_login_verification(self, response: requests.Response) -> requests.Response:
        self._assert_credential_target(str(response.url))
        with _LOGIN_LOCK:
            self._refuse_if_blocked()
            posted: requests.Response = super()._do_login_verification(  # type: ignore[misc]
                response
            )
            if _path_has(str(posted.url), "login", "account-verification"):
                self._block("account verification", str(posted.url))
        return posted

    # -- lockout state: on disk here, in memory for MemoryGuardedSession -----

    def _is_blocked(self) -> bool:
        return auth_failed_path(self).exists()

    def _mark_blocked(self) -> None:
        auth_failed_path(self).write_text("login failed\n", encoding="utf-8")

    def _blocked_message(self) -> str:
        return (
            "Smartschool rejected the login; not retrying, to avoid locking "
            "the account. Fix the credentials, then delete "
            f"{auth_failed_path(self)}"
        )

    # -- helpers ---------------------------------------------------------------

    def _credentials(self) -> Credentials:
        if self.creds is None:
            raise SmartSchoolAuthenticationError("No Smartschool credentials")
        return self.creds

    def _refuse_if_blocked(self) -> None:
        if self._is_blocked():
            raise SmartSchoolAuthenticationError(self._blocked_message())

    def _block(self, step: str, landed_on: str) -> NoReturn:
        # Only the step and the path: no query, username or answer.
        _logger.warning(
            "Smartschool rejected the %s: the response stayed on %s",
            step,
            urlparse(landed_on).path,
        )
        self._mark_blocked()
        raise SmartSchoolAuthenticationError(self._blocked_message())

    def _assert_credential_target(self, url: str) -> None:
        parsed = urlparse(url)
        # After a switch to a child on another school (children.switch), the
        # session points there; the password still belongs to the home school.
        home = self.__dict__.get("_home_host") or self._credentials().main_url
        expected = _school_host(str(home))
        actual = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or actual != expected:
            raise SmartSchoolAuthenticationError(
                "Login stopped: credentials were not posted. "
                f"Host {actual!r} is not {expected!r}."
            )


class MemoryGuardedSession(GuardedSession):
    """``GuardedSession`` for universal mode: the lockout expires on its own."""

    def _is_blocked(self) -> bool:
        creds = self._credentials()
        return memory_lockout_active(creds.username, creds.main_url)

    def _mark_blocked(self) -> None:
        creds = self._credentials()
        with _memory_lockouts_lock:
            _memory_lockouts[_memory_key(creds.username, creds.main_url)] = True

    def _blocked_message(self) -> str:
        return memory_lockout_message()
