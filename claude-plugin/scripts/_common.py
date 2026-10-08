"""Session setup and JSON output for the Smartschool Claude plugin scripts."""

from __future__ import annotations

import argparse
import atexit
import contextlib
import json
import logging
import os
import sys
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from smartschool import EnvCredentials, Smartschool, SmartSchoolAuthenticationError

from smartschool_mcp.credentials import (
    _KEYCHAIN_MFA_SERVICE as _KEYCHAIN_MFA_SERVICE,
)
from smartschool_mcp.credentials import (
    _KEYCHAIN_SERVICE as _KEYCHAIN_SERVICE,
)
from smartschool_mcp.credentials import (
    CREDENTIAL_KEYS as CREDENTIAL_KEYS,
)
from smartschool_mcp.credentials import (
    CredentialMixError as CredentialMixError,
)
from smartschool_mcp.credentials import (
    CredentialStoreError as CredentialStoreError,
)
from smartschool_mcp.credentials import (
    MissingCredentialsError as MissingCredentialsError,
)
from smartschool_mcp.credentials import (
    _apply_saved as _apply_saved,
)
from smartschool_mcp.credentials import (
    _write_private as _write_private,
)
from smartschool_mcp.credentials import (
    child_name_list as child_name_list,
)
from smartschool_mcp.credentials import (
    clear_saved_credentials as clear_saved_credentials,
)
from smartschool_mcp.credentials import (
    ensure_credentials as ensure_credentials,
)
from smartschool_mcp.credentials import (
    keychain_delete as keychain_delete,
)
from smartschool_mcp.credentials import (
    keychain_enabled as keychain_enabled,
)
from smartschool_mcp.credentials import (
    keychain_get as keychain_get,
)
from smartschool_mcp.credentials import (
    keychain_set as keychain_set,
)
from smartschool_mcp.credentials import (
    load_config as load_config,
)
from smartschool_mcp.credentials import (
    normalize_birth_date as normalize_birth_date,
)
from smartschool_mcp.credentials import (
    normalize_school as normalize_school,
)
from smartschool_mcp.credentials import (
    parse_env_file as parse_env_file,
)
from smartschool_mcp.credentials import (
    profile_cache_dir as profile_cache_dir,
)
from smartschool_mcp.credentials import (
    profile_id as profile_id,
)
from smartschool_mcp.credentials import (
    read_saved_credentials as read_saved_credentials,
)
from smartschool_mcp.credentials import (
    redact as redact,
)
from smartschool_mcp.credentials import (
    resolve_accounts as resolve_accounts,
)
from smartschool_mcp.credentials import (
    save_credentials as save_credentials,
)
from smartschool_mcp.credentials import (
    school_host as school_host,
)
from smartschool_mcp.credentials import (
    select_profiles as select_profiles,
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

_DEFAULT_SESSION_TTL = 8 * 60 * 60
_PROFILE_HELP = (
    "Profile: subdomain (dering), subdomain:username, or user@subdomain. "
    "Defaults to SMARTSCHOOL_PROFILE. Omit to use every saved profile."
)


def session_ttl_seconds() -> int:
    raw = os.environ.get("SMARTSCHOOL_SESSION_TTL", "").strip()
    if not raw:
        return _DEFAULT_SESSION_TTL
    try:
        return max(int(raw), 0)
    except ValueError:
        return _DEFAULT_SESSION_TTL


def expire_stale_session(cache: Path) -> None:
    """Drop the library cookie cache when its expiry is missing or past."""
    cookies = cache / "cookies.txt"
    meta = cache / "session.json"
    user_file = cache / "authenticated_user.yml"
    if not cookies.exists() and not meta.exists() and not user_file.exists():
        return
    expires = _read_expiry(meta)
    if expires is not None and expires > datetime.now(timezone.utc):
        return
    cookies.unlink(missing_ok=True)
    user_file.unlink(missing_ok=True)
    meta.unlink(missing_ok=True)


def _read_expiry(path: Path) -> datetime | None:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None
    if not isinstance(raw, dict):
        return None
    text = raw.get("expires_at")
    if not isinstance(text, str):
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def stamp_session(cache: Path) -> None:
    """Remember when the current cookie cache must be dropped."""
    expires = datetime.now(timezone.utc) + timedelta(seconds=session_ttl_seconds())
    _write_private(
        cache / "session.json",
        json.dumps({"expires_at": expires.isoformat()}) + "\n",
    )
    cookies = cache / "cookies.txt"
    if cookies.exists():
        os.chmod(cookies, 0o600)


def auth_failed_message(cache: Path) -> str:
    return (
        "LOGIN FAILED, niet opnieuw proberen. "
        f"Verwijder dit bestand handmatig: {cache / 'auth_failed'}"
    )


def _path_segments(url: str) -> set[str]:
    return {part for part in urlparse(url).path.split("/") if part}


def _path_has(url: str, *segments: str) -> bool:
    found = _path_segments(url)
    return any(segment in found for segment in segments)


def _acquire_session_lock(fd: int) -> Callable[[], None]:
    """Exclusive lock on an open fd. Unix uses flock; Windows uses msvcrt."""
    if os.name == "nt":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        if os.fstat(fd).st_size < 1:
            os.write(fd, b"\0")
            os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_LOCK, 1)

        def _release_windows() -> None:
            with contextlib.suppress(OSError):
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                os.close(fd)

        return _release_windows

    import fcntl

    fcntl.flock(fd, fcntl.LOCK_EX)

    def _release_unix() -> None:
        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    return _release_unix


def hold_session_lock(cache_dir: Path) -> None:
    key = str(cache_dir.resolve())
    if key in _HELD_LOCKS:
        return
    cache_dir.mkdir(parents=True, exist_ok=True)
    fd = os.open(cache_dir / ".session.lock", os.O_CREAT | os.O_RDWR, 0o600)
    release = _acquire_session_lock(fd)
    _HELD_LOCKS.add(key)

    def _release() -> None:
        _HELD_LOCKS.discard(key)
        release()

    atexit.register(_release)


class GuardedSession(Smartschool):
    """One credential POST, pinned host, shared auth_failed marker."""

    _password_posts: int = 0

    @property
    def cache_path(self) -> Path:  # type: ignore[override]
        creds = self.creds
        username = str(getattr(creds, "username", "") or "")
        main_url = str(getattr(creds, "main_url", "") or "")
        if creds is None or not username or not main_url:
            path = Path.home() / ".cache" / "smartschool"
        else:
            path = profile_cache_dir(main_url, username)
        path.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            os.chmod(path, 0o700)
        return path

    def request(self, method, url, **kwargs):  # type: ignore[override]
        self._refuse_if_blocked()
        cache = self.cache_path
        hold_session_lock(cache)
        response = super().request(method, url, **kwargs)
        self._stamp_if_authenticated(response)
        return response

    def _stamp_if_authenticated(self, response: object) -> None:
        url = str(getattr(response, "url", "") or "")
        path = urlparse(url).path
        if any(
            segment in {"login", "account-verification", "2fa"}
            for segment in path.split("/")
        ):
            return
        with contextlib.suppress(
            CredentialStoreError, SmartSchoolAuthenticationError, OSError
        ):
            stamp_session(self.cache_path)

    def confirm_login(self) -> dict:
        """Real request. The yaml user cache is not treated as proof of login.

        An empty course list or an empty body is a successful read. The
        lockout sentence is reserved for ``_block()`` after a credential POST.
        """
        self._authenticated_user = None
        log.info("Smartschool login check")
        payload = self.json("/course-list/api/v1/courses")
        cached = self._authenticated_user
        if isinstance(cached, dict) and cached:
            return cached
        if isinstance(payload, list):
            user = {"id": payload[0].get("platformId")} if payload else {}
            return self._note_live_session(user)
        if payload in ({}, None, ""):
            return self._note_live_session({})
        raise SmartSchoolAuthenticationError("Login check gaf geen vakkenlijst terug.")

    def _note_live_session(self, user: dict) -> dict:
        """Remember that a live read succeeded so XML will not refetch courses.

        ``platform_id`` indexes ``courses[0]`` and crashes on an empty list.
        A direct attribute write skips the yaml setter.
        """
        if self._authenticated_user is None:
            self._authenticated_user = user or {"checked": True}
        return user

    def _do_login(self, response):  # type: ignore[override]
        self._assert_credential_target(getattr(response, "url", ""))
        if self._password_posts >= 1 or (self.cache_path / "auth_failed").exists():
            self._block()
        self._password_posts += 1
        log.info("Smartschool login attempt")
        posted = super()._do_login(response)
        posted_url = str(getattr(posted, "url", ""))
        # Birthday/2FA are the next step. Staying on /login, including
        # /login?error=1, means the password was rejected.
        if _path_has(posted_url, "login"):
            log.warning("Smartschool login failed")
            self._block()
        log.info("Smartschool login form accepted")
        return posted

    def _do_login_verification(self, response):  # type: ignore[override]
        self._assert_credential_target(getattr(response, "url", ""))
        log.info("Smartschool account verification")
        posted = super()._do_login_verification(response)
        posted_url = str(getattr(posted, "url", ""))
        if _path_has(posted_url, "login", "account-verification"):
            log.warning("Smartschool login verification failed")
            self._block()
        return posted

    def _refuse_if_blocked(self) -> None:
        self._require_credentials()
        if (self.cache_path / "auth_failed").exists():
            raise SmartSchoolAuthenticationError(auth_failed_message(self.cache_path))

    def _block(self) -> None:
        self._require_credentials()
        path = self.cache_path / "auth_failed"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("login failed\n", encoding="utf-8")
        raise SmartSchoolAuthenticationError(auth_failed_message(self.cache_path))

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


class _FailedSession:
    """A profile that was not opened. No request is made for it."""

    def __init__(self, account: Mapping[str, Any], message: str) -> None:
        self.open_error = message
        self.child_names = child_name_list(account)
        try:
            self.profile_id: str | None = profile_id(
                str(account.get("main_url", "")), str(account.get("username", ""))
            )
        except (SmartSchoolAuthenticationError, CredentialStoreError):
            self.profile_id = None
        self.creds = type(
            "_Creds",
            (),
            {
                "main_url": str(account.get("main_url", "")),
                "username": str(account.get("username", "")),
            },
        )()


def _open_one(account: Mapping[str, Any]) -> GuardedSession:
    _apply_saved(account)
    credentials = EnvCredentials()
    credentials.validate()
    # The library builds "https://" + main_url. Store the bare host so a
    # configured https://school.smartschool.be does not become https://https://…
    host = school_host(credentials.main_url)
    object.__setattr__(credentials, "main_url", host)
    cache = profile_cache_dir(credentials.main_url, credentials.username)
    expire_stale_session(cache)
    if (cache / "auth_failed").exists():
        raise SmartSchoolAuthenticationError(auth_failed_message(cache))
    hold_session_lock(cache)
    session = GuardedSession(credentials)
    with contextlib.suppress(AttributeError):
        session.profile_id = profile_id(credentials.main_url, credentials.username)  # type: ignore[attr-defined]
        session.child_names = child_name_list(account)  # type: ignore[attr-defined]
    return session


def open_sessions() -> list[Any]:
    """One session per selected profile. A blocked profile is not retried."""
    accounts = resolve_accounts()
    opened: list[Any] = []
    for account in accounts:
        try:
            opened.append(_open_one(account))
        except Exception as exc:
            if len(accounts) == 1:
                raise
            opened.append(
                _FailedSession(account, redact(f"{type(exc).__name__}: {exc}"))
            )
    return opened


def open_session() -> GuardedSession:
    """One locked session. Refuses when a previous login already failed."""
    sessions = open_sessions()
    if len(sessions) != 1:
        labels = ", ".join(
            str(getattr(session, "profile_id", "") or "?") for session in sessions
        )
        raise MissingCredentialsError(
            f"Meerdere profielen ({labels}). Kies met --profile of "
            "SMARTSCHOOL_PROFILE. Geen loginpoging gedaan."
        )
    session = sessions[0]
    error = getattr(session, "open_error", None)
    if isinstance(error, str) and error:
        raise MissingCredentialsError(error)
    return session


def profile_public(session: object) -> dict[str, Any]:
    """Child label for JSON. Names are not passwords."""
    names = getattr(session, "child_names", None)
    profile = getattr(session, "profile_id", None)
    if not isinstance(names, list):
        names = []
    clean = [name for name in names if isinstance(name, str) and name.strip()]
    if not isinstance(profile, str) or not profile:
        profile = None
    return {
        "profile": profile,
        "child": clean[0] if clean else None,
        "children": clean,
    }


def combine_profiles(
    sessions: list[Any], fetch: Callable[[Any], dict[str, Any]]
) -> dict[str, Any]:
    """Run ``fetch`` once per profile. Several profiles come back under ``profiles``."""

    def run(session: Any) -> dict[str, Any]:
        error = getattr(session, "open_error", None)
        if isinstance(error, str) and error:
            payload: dict[str, Any] = {"error": error}
        else:
            try:
                payload = fetch(session)
            except Exception as exc:
                if len(sessions) == 1:
                    raise
                payload = {"error": redact(f"{type(exc).__name__}: {exc}")}
        payload.update(profile_public(session))
        return payload

    if len(sessions) == 1:
        return run(sessions[0])
    return {"profiles": [run(session) for session in sessions]}


def add_profile_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--profile", default=None, help=_PROFILE_HELP)


def use_profile_argument(args: argparse.Namespace) -> None:
    chosen = getattr(args, "profile", None)
    if isinstance(chosen, str) and chosen.strip():
        os.environ["SMARTSCHOOL_PROFILE"] = chosen.strip()


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
    """Same fields the MCP ``get_schedule`` tool returns for one planner row.

    Schedule scripts pass raw calendar JSON through
    ``smartschool_mcp.planner_fields.fetch_calendar`` instead. This helper
    remains for library objects that already parsed ``id`` / ``description`` /
    ``upload_folders``.
    """
    period = getattr(element, "period", None)
    start = getattr(period, "date_time_from", None) if period else None
    end = getattr(period, "date_time_to", None) if period else None
    assignment_type = getattr(element, "assignment_type", None)
    courses = getattr(element, "courses", None) or []
    locations = getattr(element, "locations", None) or []
    description = getattr(element, "description", None)
    if not isinstance(description, str) or not description.strip():
        description = getattr(element, "public_info", None)
    if not isinstance(description, str):
        description = ""
    element_id = getattr(element, "id", None)
    platform_id = getattr(element, "platform_id", None)
    return {
        "id": element_id if isinstance(element_id, str) else None,
        "platform_id": platform_id if isinstance(platform_id, int) else None,
        "name": getattr(element, "name", "") or "",
        "description": description.strip(),
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
        "upload_folders": _public_upload_folders(element),
    }


def _public_upload_folders(element: object) -> list[dict[str, Any]]:
    folders = getattr(element, "upload_folders", None)
    if not isinstance(folders, list):
        return []
    public: list[dict[str, Any]] = []
    for folder in folders:
        files = getattr(folder, "files", None)
        file_rows: list[dict[str, Any]] = []
        if isinstance(files, list):
            for item in files:
                download = getattr(item, "download_url", None)
                size = getattr(item, "size", None)
                file_rows.append(
                    {
                        "id": str(getattr(item, "id", "") or ""),
                        "name": str(getattr(item, "name", "") or ""),
                        "mime_type": str(getattr(item, "mime_type", "") or ""),
                        "size": size if isinstance(size, int) else None,
                        "has_download_url": isinstance(download, str)
                        and bool(download),
                    }
                )
        public.append(
            {
                "id": str(getattr(folder, "id", "") or ""),
                "name": str(getattr(folder, "name", "") or ""),
                "files": file_rows,
            }
        )
    return public


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
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
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


def _payload_failed(payload: Mapping[str, Any]) -> bool:
    if "error" in payload:
        return True
    profiles = payload.get("profiles")
    if not isinstance(profiles, list):
        return False
    return any(isinstance(item, dict) and "error" in item for item in profiles)


def emit(payload: Mapping[str, Any]) -> int:
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2, default=str)
    sys.stdout.write("\n")
    return 1 if _payload_failed(payload) else 0


def main(build: Callable[[], Mapping[str, Any]]) -> None:
    try:
        code = emit(build())
    except Exception as exc:
        code = emit({"error": redact(f"{type(exc).__name__}: {exc}")})
    raise SystemExit(code)
