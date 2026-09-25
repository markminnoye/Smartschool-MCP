"""Session setup and JSON output for the Smartschool Claude plugin scripts."""

from __future__ import annotations

import atexit
import contextlib
import fcntl
import json
import logging
import os
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from smartschool import EnvCredentials, Smartschool, SmartSchoolAuthenticationError

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
CREDENTIAL_KEYS = (
    "SMARTSCHOOL_MAIN_URL",
    "SMARTSCHOOL_USERNAME",
    "SMARTSCHOOL_PASSWORD",
    "SMARTSCHOOL_MFA",
)
_HELD_LOCKS: set[str] = set()
log = logging.getLogger("smartschool_plugin")


def _configure_logs() -> None:
    """Log login steps without the library's username/password lines."""
    try:
        from logprise import logger
    except ImportError:
        return
    logger.disable("smartschool")
    if not log.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        log.addHandler(handler)
        log.setLevel(logging.INFO)


_configure_logs()


class CredentialMixError(RuntimeError):
    """Env and config.env disagree. No login is attempted."""


def parse_env_file(text: str) -> dict[str, str]:
    """Parse a dotenv-style file. Values are not shell-expanded."""
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def load_config(path: Path | None = None) -> Path | None:
    """Load KEY=VALUE config into the environment.

    Existing environment variables win, so a shell export is not overwritten.
    ``SMARTSCHOOL_CONFIG`` selects a file explicitly. Otherwise
    ``<plugin>/config.env`` is used when it exists.
    """
    if path is not None:
        chosen = path
        if not chosen.is_file():
            raise FileNotFoundError(f"Credential file not found: {chosen}")
    else:
        explicit = os.environ.get("SMARTSCHOOL_CONFIG", "").strip()
        if explicit:
            chosen = Path(explicit).expanduser()
            if not chosen.is_file():
                raise FileNotFoundError(f"Credential file not found: {chosen}")
        else:
            chosen = PLUGIN_ROOT / "config.env"
            if not chosen.is_file():
                return None

    parsed = parse_env_file(chosen.read_text(encoding="utf-8"))
    _apply_credentials(parsed)
    for key, value in parsed.items():
        if key not in CREDENTIAL_KEYS:
            os.environ.setdefault(key, value)
    return chosen


def _credential_map(source: Mapping[str, str]) -> dict[str, str]:
    return {key: source[key] for key in CREDENTIAL_KEYS if key in source}


def _apply_credentials(file_values: Mapping[str, str]) -> None:
    """Refuse when env and config.env would mix two accounts. No network."""
    from_file = _credential_map(file_values)
    from_env = _credential_map(os.environ)
    if from_file and from_env and from_file != from_env:
        raise CredentialMixError(
            "LOGIN STOPPED, geen poging gedaan. config.env en de environment "
            "geven verschillende Smartschool-gegevens. Gebruik één bron en "
            "wis de andere. Niet opnieuw proberen met een mix."
        )
    if from_file:
        for key in CREDENTIAL_KEYS:
            if key in from_file:
                os.environ[key] = from_file[key]
            else:
                os.environ.pop(key, None)


def school_host(main_url: str) -> str:
    """Host of SMARTSCHOOL_MAIN_URL. https only, name must end in .smartschool.be."""
    raw = main_url.strip()
    if "://" in raw:
        parsed = urlparse(raw)
        if parsed.scheme != "https":
            raise SmartSchoolAuthenticationError("SMARTSCHOOL_MAIN_URL moet https zijn")
        host = parsed.hostname or ""
    else:
        host = raw.split("/")[0].split(":")[0]
    host = host.lower()
    if not host.endswith(".smartschool.be"):
        raise SmartSchoolAuthenticationError(
            "SMARTSCHOOL_MAIN_URL moet eindigen op .smartschool.be"
        )
    return host


def _cache_dir(username: str) -> Path:
    return Path.home() / ".cache" / "smartschool" / username


def _auth_failed_path(username: str) -> Path:
    return _cache_dir(username) / "auth_failed"


def auth_failed_message(username: str) -> str:
    return (
        "LOGIN FAILED, niet opnieuw proberen. "
        f"Verwijder dit bestand handmatig: {_auth_failed_path(username)}"
    )


def hold_session_lock(cache_dir: Path) -> None:
    key = str(cache_dir.resolve())
    if key in _HELD_LOCKS:
        return
    cache_dir.mkdir(parents=True, exist_ok=True)
    fd = os.open(cache_dir / ".session.lock", os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    _HELD_LOCKS.add(key)

    def _release() -> None:
        _HELD_LOCKS.discard(key)
        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    atexit.register(_release)


class GuardedSession(Smartschool):
    """One credential POST, pinned host, shared auth_failed marker."""

    _password_posts: int = 0

    def request(self, method, url, **kwargs):  # type: ignore[override]
        self._refuse_if_blocked()
        cache = self.cache_path
        hold_session_lock(cache)
        return super().request(method, url, **kwargs)

    def confirm_login(self) -> dict:
        """Real request. The yaml user cache is not treated as proof of login."""
        self._authenticated_user = None
        log.info("Smartschool login check")
        payload = self.json("/course-list/api/v1/courses")
        cached = self._authenticated_user
        if isinstance(cached, dict) and cached:
            return cached
        if isinstance(payload, list) and payload:
            return {"id": payload[0].get("platformId")}
        raise SmartSchoolAuthenticationError(
            auth_failed_message(self._require_credentials().username)
        )

    def _do_login(self, response):  # type: ignore[override]
        self._assert_credential_target(getattr(response, "url", ""))
        if self._password_posts >= 1 or _auth_failed_path(self.creds.username).exists():
            self._block()
        self._password_posts += 1
        log.info("Smartschool login attempt")
        posted = super()._do_login(response)
        if str(getattr(posted, "url", "")).rstrip("/").endswith("/login"):
            log.warning("Smartschool login failed")
            self._block()
        log.info("Smartschool login form accepted")
        return posted

    def _do_login_verification(self, response):  # type: ignore[override]
        self._assert_credential_target(getattr(response, "url", ""))
        log.info("Smartschool account verification")
        return super()._do_login_verification(response)

    def _refuse_if_blocked(self) -> None:
        username = self._require_credentials().username
        if _auth_failed_path(username).exists():
            raise SmartSchoolAuthenticationError(auth_failed_message(username))

    def _block(self) -> None:
        username = self._require_credentials().username
        path = _auth_failed_path(username)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("login failed\n", encoding="utf-8")
        raise SmartSchoolAuthenticationError(auth_failed_message(username))

    def _assert_credential_target(self, url: str) -> None:
        parsed = urlparse(url)
        expected = school_host(self._require_credentials().main_url)
        actual = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or actual != expected:
            log.warning("Smartschool login stopped; credentials were not posted")
            raise SmartSchoolAuthenticationError(
                "Login stopped: credentials were not posted. "
                f"Host {actual!r} is not {expected!r}."
            )


def open_session() -> GuardedSession:
    """One locked session. Refuses when a previous login already failed."""
    load_config()
    credentials = EnvCredentials()
    credentials.validate()
    school_host(credentials.main_url)
    cache = _cache_dir(credentials.username)
    if _auth_failed_path(credentials.username).exists():
        raise SmartSchoolAuthenticationError(auth_failed_message(credentials.username))
    hold_session_lock(cache)
    return GuardedSession(credentials)


def format_date(value: Any) -> str | None:
    try:
        return value.strftime("%Y-%m-%d") if value else None
    except (AttributeError, ValueError):
        return None


def csv_or_none(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def teacher_names(teachers: list[Any] | None) -> list[str]:
    if not teachers:
        return []
    names: list[str] = []
    for teacher in teachers:
        name = getattr(teacher, "name", None)
        label = getattr(name, "starting_with_last_name", None)
        if label:
            names.append(str(label))
    return names


def organiser_names(element: object) -> list[str]:
    organisers = getattr(element, "organisers", None)
    users = getattr(organisers, "users", None) or []
    names: list[str] = []
    for user in users:
        name = getattr(user, "name", None)
        first = getattr(name, "starting_with_first_name", None)
        if first:
            names.append(str(first))
        elif name:
            names.append(str(name))
    return names


def planned_element(element: object) -> dict[str, Any]:
    """Same fields the MCP ``get_schedule`` tool returns for one planner row."""
    period = getattr(element, "period", None)
    start = getattr(period, "date_time_from", None) if period else None
    end = getattr(period, "date_time_to", None) if period else None
    assignment_type = getattr(element, "assignment_type", None)
    courses = getattr(element, "courses", None) or []
    locations = getattr(element, "locations", None) or []
    return {
        "name": getattr(element, "name", "") or "",
        "type": getattr(element, "planned_element_type", None),
        "from": start.strftime("%Y-%m-%d %H:%M") if start else None,
        "to": end.strftime("%Y-%m-%d %H:%M") if end else None,
        "whole_day": getattr(period, "whole_day", None) if period else None,
        "color": getattr(element, "color", None),
        "courses": [getattr(course, "name", str(course)) for course in courses],
        "locations": [getattr(loc, "title", str(loc)) for loc in locations],
        "organisers": organiser_names(element),
        "unconfirmed": getattr(element, "unconfirmed", None),
        "pinned": getattr(element, "pinned", None),
        "assignment_type": (
            assignment_type.name if assignment_type is not None else None
        ),
    }


def _display_name(value: object) -> str | None:
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if not isinstance(value, dict):
        return None
    for key in (
        "startingWithFirstName",
        "starting_with_first_name",
        "fullName",
        "fullname",
    ):
        text = value.get(key)
        if isinstance(text, str) and text.strip():
            return text.strip()
    return None


def public_user(user: object) -> dict[str, Any]:
    """Fields safe to print. Drops secrets and nested portal objects."""
    if not isinstance(user, dict):
        return {}
    safe: dict[str, Any] = {}
    user_id = user.get("id")
    if user_id not in (None, ""):
        safe["id"] = user_id
    name = (
        _display_name(user.get("name"))
        or _display_name(user.get("fullname"))
        or _display_name(user.get("fullName"))
    )
    if name:
        safe["name"] = name
    username = user.get("username")
    if isinstance(username, str) and username.strip():
        safe["username"] = username.strip()
    return safe


def emit(payload: Mapping[str, Any]) -> int:
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2, default=str)
    sys.stdout.write("\n")
    return 1 if "error" in payload else 0


def main(build: Callable[[], Mapping[str, Any]]) -> None:
    try:
        code = emit(build())
    except Exception as exc:
        code = emit({"error": f"{type(exc).__name__}: {exc}"})
    raise SystemExit(code)
