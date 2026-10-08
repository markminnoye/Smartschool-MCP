"""
Smartschool MCP Server
Provides tools to interact with Smartschool API for courses, results, tasks,
messages and more.
"""

from __future__ import annotations

import os
import re
import threading
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from functools import lru_cache
from typing import Any, TypedDict
from urllib.parse import urljoin, urlparse

from cachetools import TTLCache, cached
from mcp.server.fastmcp import FastMCP
from smartschool import (
    AppCredentials,
    Attachments,
    BoxType,
    CourseCondensed,
    Courses,
    EnvCredentials,
    FolderItem,
    FutureTasks,
    Message,
    MessageHeaders,
    Periods,
    PlannedElements,
    Reports,
    Results,
    Smartschool,
    SmartSchoolAuthenticationError,
    StudentSupportLinks,
    TopNavCourses,
)

from smartschool_mcp.credentials import (
    NO_DESKTOP_CREDENTIALS,
    CredentialMixError,
    CredentialStoreError,
    MissingCredentialsError,
    prepare_server_credentials,
)
from smartschool_mcp.filenames import filename_from_response, header_value
from smartschool_mcp.guard import (
    GuardedSession,
    auth_failed_message,
    auth_failed_path,
    normalize_school_main_url,
)
from smartschool_mcp.planner_fields import (
    ASSIGNMENT_DETAIL_TYPES,
    PLANNER_ATTACHMENT_INCLUDES,
    attachment_diagnostic,
    calendar_element_dict,
    element_id_or_none,
    fetch_assignment_detail,
    fetch_calendar,
    fetch_calendar_raw,
    file_download,
    json_diagnostic,
    planner_download_candidates,
)
from smartschool_mcp.result_stats import (
    detail_diagnostic,
    statistics_from_detail,
    statistics_from_payload,
)


def _named_child() -> str:
    """Child name from the shared profile, or from an explicit env override.

    The Desktop bundle does not collect a second password.
    ``activate_saved_credentials`` copies the saved child into
    ``SMARTSCHOOL_CHILD``. Instructions are built at import, before that
    copy, so a single saved profile is read here as well.
    ``SMARTSCHOOL_CHILD_NAME`` remains a process override for hosts that set it.
    """
    for key in ("SMARTSCHOOL_CHILD", "SMARTSCHOOL_CHILD_NAME"):
        child = " ".join(os.environ.get(key, "").split())
        if child:
            return child
    try:
        from smartschool_mcp.credentials import (
            child_name_list,
            load_profiles,
            select_profiles,
        )

        profiles = load_profiles()
        selector = os.environ.get("SMARTSCHOOL_PROFILE", "").strip()
        username = (
            os.environ.get("SMARTSCHOOL_USERNAME", "").strip()
            or os.environ.get("SMARTSCHOOL_USER", "").strip()
        )
        if selector:
            profiles = select_profiles(profiles, selector)
        elif username:
            profiles = [
                profile
                for profile in profiles
                if str(profile.get("username", "")) == username
            ]
    except Exception:
        return ""
    if len(profiles) != 1:
        return ""
    names = child_name_list(profiles[0])
    if not names:
        return ""
    return " ".join(names[0].split())


def _server_instructions() -> str | None:
    """Tell the model which saved child ``switch_child`` should select."""
    child = _named_child()
    if not child:
        return None
    return (
        f"The Smartschool account in this session is for the child {child}. "
        "Before reading the timetable, grades, tasks, or messages, call "
        "get_children and switch_child to that child when they are not "
        "already the current child. "
        "A failed login is stored as auth_failed; do not submit the password again."
    )


# MCP server - tools are registered via @mcp.tool() decorators below
mcp = FastMCP("Smartschool MCP", instructions=_server_instructions())


class AuthenticationError(RuntimeError):
    """Authentication state is present but credentials are no longer valid."""


def _tool_error(exc: Exception, prefix: str) -> str:
    """Parent-facing credential errors stay Dutch. Other failures keep the prefix."""
    if isinstance(
        exc, (MissingCredentialsError, CredentialStoreError, CredentialMixError)
    ):
        return str(exc)
    text = str(exc)
    if "Please verify and correct these attributes" in text:
        return NO_DESKTOP_CREDENTIALS
    return f"{prefix}: {text}"


def _open_env_session() -> Smartschool:
    """Env-var session with a normalised school host and the login lockout.

    A bare subdomain (``dering``) becomes ``dering.smartschool.be``. A pasted
    ``https://…smartschool.be`` URL is reduced to that host. An existing
    ``auth_failed`` file refuses before any credential POST.
    """
    creds = EnvCredentials()
    if creds.main_url.strip():
        from smartschool_mcp.auth import _validate_school_url

        normalized = normalize_school_main_url(creds.main_url)
        validated = _validate_school_url(normalized)
        if not validated:
            raise ValueError("Untrusted or invalid Smartschool host")
        object.__setattr__(creds, "main_url", validated)
    username = creds.username.strip()
    if username and auth_failed_path(username, creds.main_url).exists():
        raise SmartSchoolAuthenticationError(
            auth_failed_message(username, creds.main_url)
        )
    return GuardedSession(creds)


@lru_cache(maxsize=1)
def _env_session() -> Smartschool:
    """Cached session using environment-variable credentials (single-user mode).

    Lazy-initialized on first tool invocation so that import-time errors
    (missing env vars, network failures) surface as tool errors rather than
    crashing the process on startup.
    """
    prepare_server_credentials()
    return _open_env_session()


# How long (seconds) a cached Smartschool session is reused before the next
# request for those credentials creates a fresh one.  Cookie-based sessions
# expire server-side; keeping this below the server's idle-session timeout
# prevents stale-cookie failures.  Override with SESSION_TTL_SECONDS env var.
_SESSION_TTL_SECONDS = int(os.environ.get("SESSION_TTL_SECONDS", "3600"))
_session_cache: TTLCache[tuple[str, str, str, str], Smartschool] = TTLCache(
    maxsize=256, ttl=_SESSION_TTL_SECONDS
)
_session_cache_lock = threading.Lock()


@cached(cache=_session_cache, lock=_session_cache_lock)
def _cached_app_session(
    username: str, password: str, main_url: str, mfa: str
) -> Smartschool:
    """TTL-cached session per unique credential tuple (universal mode).

    Entries expire after SESSION_TTL_SECONDS (default 3600) so stale cookies
    from server-side session expiry are automatically replaced.  Bounded to
    256 entries to cap memory use.
    """
    from smartschool_mcp.auth import _validate_school_url

    validated_host = _validate_school_url(main_url)
    if not validated_host:
        raise ValueError("Untrusted or invalid Smartschool host")
    if auth_failed_path(username, validated_host).exists():
        raise SmartSchoolAuthenticationError(
            auth_failed_message(username, validated_host)
        )

    return GuardedSession(
        AppCredentials(
            username=username,
            password=password,
            main_url=validated_host,
            mfa=mfa,
        )
    )


def _session() -> Smartschool:
    """Return the active Smartschool session.

    In universal mode (OAuth), the access token carries a ``cred_key`` that
    maps to stored Smartschool credentials.  Sessions are cached per unique
    credential tuple so repeated calls reuse the same authenticated session.
    Falls back to the environment-variable session in single-user / stdio mode.
    """
    # Check OAuth context (universal mode via OAuth 2.1)
    try:
        from mcp.server.auth.middleware.auth_context import get_access_token

        from smartschool_mcp.auth import get_credentials

        access_token = get_access_token()
        if access_token is not None and hasattr(access_token, "cred_key"):
            creds = get_credentials(access_token.cred_key)  # type: ignore[attr-defined]
            if creds is None:
                raise AuthenticationError(
                    "OAuth credentials expired; re-authentication required"
                )
            return _cached_app_session(
                creds.username, creds.password, creds.main_url, creds.mfa
            )
    except ImportError:
        pass

    return _env_session()


def _safe_get_teacher_names(
    teachers: list | None,
    use_last_name: bool = True,
) -> list[str]:
    """Safely extract teacher names from teacher objects."""
    if not teachers:
        return []

    try:
        if use_last_name:
            return [teacher.name.starting_with_last_name for teacher in teachers]
        else:
            return [teacher.name.starting_with_first_name for teacher in teachers]
    except (AttributeError, IndexError):
        return []


def _safe_format_date(date_obj: Any) -> str | None:
    """Safely format date objects to string."""
    try:
        return date_obj.strftime("%Y-%m-%d") if date_obj else None
    except (AttributeError, ValueError):
        return None


def _csv_or_none(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


_ACCOUNT_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_GOTOURL_RE = re.compile(
    r"/Studentcard/Chain/gotourl/accountID/([A-Za-z0-9_-]+)"
    r"""[^>]*>\s*(?:<img\b[^>]*>\s*)?<span>([^<]+)</span>""",
    re.IGNORECASE | re.DOTALL,
)
_STUDENTCARD_XHR_HEADERS = {
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json",
}
_CHILD_LIST_KEYS = (
    "students",
    "children",
    "accounts",
    "items",
    "data",
    "results",
    "list",
)


def _pick(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in data and data[key] not in (None, ""):
            return data[key]
    return None


def _string_field(data: dict[str, Any], *keys: str) -> str | None:
    value = _pick(data, *keys)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _safe_account_id(account_id: str | int) -> str | None:
    stripped = str(account_id).strip()
    if not stripped or stripped == "0" or not _ACCOUNT_ID_RE.fullmatch(stripped):
        return None
    return stripped


def _as_child_records(payload: Any) -> list[dict[str, Any]]:
    """Unwrap POST /Studentcard/Student/getStudents into a list of dicts."""
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in _CHILD_LIST_KEYS:
        inner = payload.get(key)
        if isinstance(inner, list):
            return [item for item in inner if isinstance(item, dict)]
    if any(
        key in payload
        for key in ("accountID", "accountId", "account_id", "firstName", "lastName")
    ):
        return [payload]
    return []


def _first_name_value(data: dict[str, Any]) -> str | None:
    first = _string_field(data, "firstName", "first_name", "voornaam")
    if first:
        return first
    name = data.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return None


def _person_name(data: dict[str, Any]) -> str | None:
    bin_name = _string_field(data, "fullNameBIN", "full_name_bin")
    if bin_name:
        return bin_name
    first = _first_name_value(data)
    last = _string_field(data, "lastName", "last_name", "surname", "naam")
    if first and last:
        return f"{first} {last}".strip()
    full = _string_field(data, "fullName", "full_name")
    if full:
        return full
    name = data.get("name")
    if isinstance(name, dict):
        nested = _pick(
            name,
            "starting_with_first_name",
            "startingWithFirstName",
            "firstName",
        )
        if isinstance(nested, str) and nested.strip():
            return nested.strip()
        nested = _pick(
            name,
            "starting_with_last_name",
            "startingWithLastName",
            "lastName",
        )
        if isinstance(nested, str) and nested.strip():
            return nested.strip()
        return None
    if first:
        return first
    if last:
        return last
    return None


def _person_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Map a Studentcard / authenticatedUser object to stable MCP fields."""
    account_id = _safe_account_id(
        _pick(data, "accountID", "accountId", "account_id") or ""
    )
    user_id = _pick(data, "id", "userID", "userId", "user_id")
    return {
        "account_id": account_id,
        "user_id": None if user_id is None else str(user_id),
        "username": _pick(data, "username", "userName", "user_name"),
        "name": _person_name(data),
        "first_name": _first_name_value(data),
        "last_name": _string_field(data, "lastName", "last_name", "surname"),
        "class_name": _string_field(data, "className", "class_name", "class", "klas"),
        "platform": _pick(
            data,
            "platform",
            "platformUrl",
            "platform_url",
            "mainUrl",
            "main_url",
        ),
        "is_current": _pick(
            data, "isCurrentUser", "isCurrent", "is_current", "current"
        ),
    }


def _empty_person(
    *,
    account_id: str | None = None,
    name: str | None = None,
    first_name: str | None = None,
) -> dict[str, Any]:
    return {
        "account_id": account_id,
        "user_id": None,
        "username": None,
        "name": name,
        "first_name": first_name,
        "last_name": None,
        "class_name": None,
        "platform": None,
        "is_current": None,
    }


def _children_from_topnav(html: str) -> list[dict[str, Any]]:
    """Parse Mijn kinderen switch links from the Studentcard HTML shell."""
    children: list[dict[str, Any]] = []
    if not isinstance(html, str) or not html:
        return children
    seen: set[str] = set()
    for match in _GOTOURL_RE.finditer(html):
        account_id = _safe_account_id(match.group(1))
        first_name = match.group(2).strip()
        if not account_id or not first_name or account_id in seen:
            continue
        seen.add(account_id)
        children.append(
            _empty_person(
                account_id=account_id,
                name=first_name,
                first_name=first_name,
            )
        )
    return children


def _merge_topnav_children(
    json_children: list[dict[str, Any]],
    html_children: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Fill missing switch ids from topnav; keep HTML-only siblings."""
    used: set[str] = set()
    merged: list[dict[str, Any]] = []
    for child in json_children:
        updated = dict(child)
        if not updated.get("account_id"):
            first = (
                (updated.get("first_name") or updated.get("name") or "").strip().lower()
            )
            for html_child in html_children:
                html_id = html_child.get("account_id")
                html_name = (html_child.get("first_name") or "").strip().lower()
                if html_id and html_id not in used and first and first == html_name:
                    updated["account_id"] = html_id
                    used.add(html_id)
                    break
        account_id = updated.get("account_id")
        if isinstance(account_id, str) and account_id:
            used.add(account_id)
        merged.append(updated)
    for html_child in html_children:
        html_id = html_child.get("account_id")
        if isinstance(html_id, str) and html_id and html_id not in used:
            merged.append(html_child)
            used.add(html_id)
    return merged


def _current_user_dict(session: Smartschool) -> dict[str, Any] | None:
    try:
        user = session.authenticated_user
    except Exception:
        return None
    if not isinstance(user, dict):
        return None
    return _person_dict(user)


def _mark_current_child(
    child: dict[str, Any], current: dict[str, Any] | None
) -> dict[str, Any]:
    if child.get("is_current") in (True, False):
        return child
    if current is None:
        return child
    for key in ("account_id", "user_id", "username"):
        left = child.get(key)
        right = current.get(key)
        if left is not None and right is not None and str(left) == str(right):
            child["is_current"] = True
            return child
    child["is_current"] = False
    return child


def _authenticated_user_from_response(
    session: Smartschool, response: Any
) -> dict | None:
    html = _parse_html(response)
    try:
        from smartschool._common import parse_smsc_vars
    except ImportError:
        return None
    for script in html.select("script"):
        if script.get("src"):
            continue
        text = script.string or script.get_text() or ""
        if "authenticatedUser" not in text:
            continue
        user = parse_smsc_vars(text).get("authenticatedUser")
        if isinstance(user, dict):
            session.authenticated_user = user
            return user
    return None


_AUTH_PATH_SEGMENTS = {"login", "account-verification", "2fa"}
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}


def _url_is_auth(url: str) -> bool:
    return bool(_AUTH_PATH_SEGMENTS & set(urlparse(url).path.split("/")))


def _url_is_login(url: str) -> bool:
    return "login" in set(urlparse(url).path.split("/"))


def _is_foreign_host(url: str, origin_host: str) -> bool:
    host = urlparse(url).netloc
    return bool(host and origin_host and host != origin_host)


def _is_foreign_login(url: str, origin_host: str) -> bool:
    """True when ``url`` is a login page on a host other than the origin session."""
    return _url_is_login(url) and _is_foreign_host(url, origin_host)


_BROWSER_NAV_HEADERS = {
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Upgrade-Insecure-Requests": "1",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Safari/537.36"
    ),
}


def _requests_parent_request(session: Smartschool) -> Any | None:
    """Return ``requests.Session.request`` if ``session`` is a real Session."""
    for cls in type(session).__mro__:
        if cls.__name__ == "Session" and getattr(cls, "__module__", "").startswith(
            "requests"
        ):
            request = getattr(cls, "request", None)
            if callable(request):
                return request
    return None


def _raw_session_request(
    session: Smartschool, method: str, url: str, **kwargs: Any
) -> Any | None:
    """HTTP without Smartschool's login-form POST interceptor."""
    parent_request = _requests_parent_request(session)
    if parent_request is None:
        return None
    response = parent_request(session, method, url, **kwargs)
    cookies = getattr(session, "cookies", None)
    save = getattr(cookies, "save", None)
    if callable(save):
        try:
            save(ignore_discard=True)
        except Exception:
            pass
    return response


def _foreign_hop_get(session: Smartschool, url: str, origin_host: str) -> Any:
    """GET another school's hop the way the browser does, without posting login."""
    headers = dict(_BROWSER_NAV_HEADERS)
    if origin_host:
        headers["Referer"] = f"https://{origin_host}/"
    raw = _raw_session_request(
        session, "GET", url, allow_redirects=False, headers=headers
    )
    if raw is not None:
        return raw
    return session.get(url, allow_redirects=False)


def _refuse_password_login(*_args: Any, **_kwargs: Any) -> Any:
    raise RuntimeError("refusing foreign password login")


def _refuse_foreign_2fa(*_args: Any, **_kwargs: Any) -> Any:
    raise RuntimeError("refusing foreign 2fa")


@contextmanager
def _without_password_or_2fa(session: Smartschool):
    """Let account-verification run; block /login POST and TOTP."""
    original_login = getattr(session, "_do_login", None)
    original_2fa = getattr(session, "_complete_verification_2fa", None)
    session._do_login = _refuse_password_login  # type: ignore[method-assign]
    session._complete_verification_2fa = _refuse_foreign_2fa  # type: ignore[method-assign]
    try:
        yield
    finally:
        if original_login is not None:
            session._do_login = original_login  # type: ignore[method-assign]
        if original_2fa is not None:
            session._complete_verification_2fa = original_2fa  # type: ignore[method-assign]


def _retarget_session_host(session: Smartschool, url: str) -> str | None:
    """Point the session at another Smartschool host after a child-chain hop."""
    new_host = urlparse(url).netloc
    if not new_host:
        return None
    try:
        current_host = urlparse(session.create_url("/")).netloc
    except Exception:
        current_host = ""
    if not current_host or new_host == current_host:
        return None
    creds = getattr(session, "creds", None)
    if creds is None or not hasattr(creds, "main_url"):
        return new_host
    object.__setattr__(creds, "main_url", new_host)
    session.__dict__.pop("_url", None)
    session.__dict__.pop("platform_id", None)
    return new_host


def _follow_switched_host(session: Smartschool, response: Any) -> str | None:
    """If Mijn kinderen jumped to another school host, retarget the session."""
    return _retarget_session_host(session, getattr(response, "url", "") or "")


def _redirect_location(response: Any) -> str | None:
    status = getattr(response, "status_code", None)
    if status not in _REDIRECT_STATUSES:
        return None
    headers = getattr(response, "headers", None) or {}
    location = headers.get("Location") or headers.get("location")
    if not isinstance(location, str) or not location.strip():
        return None
    current = str(getattr(response, "url", "") or "")
    return urljoin(current, location.strip())


def _follow_child_switch_redirects(
    session: Smartschool, start_path: str
) -> tuple[Any, str | None, str | None]:
    """Follow gotourl hops without replaying the original host after OTP/auth.

    Cross-school children 302 to ``https://other.smartschool.be/otp/...``. The
    smartschool session would then finish De Ring login and replay the original
    De Pass gotourl, dropping the new cookies. Hop-by-hop avoids that replay.

    Never GET another school's ``/login`` through ``session.get``:
    ``Smartschool.request`` POSTs this session's username/password on any
    login form. Foreign hops (``/otp/...``, then relative ``/Studentcard``)
    use a raw GET with browser navigation headers. Foreign
    ``account-verification`` is allowed with password login and TOTP disabled.
    """
    origin_host = urlparse(session.create_url("/")).netloc
    url: str = start_path
    switched_host: str | None = None
    response: Any = None
    for _ in range(12):
        if _is_foreign_login(url, origin_host):
            return response, switched_host, url
        if _is_foreign_host(url, origin_host) and _url_is_auth(url):
            switched_host = _retarget_session_host(session, url) or switched_host
            try:
                with _without_password_or_2fa(session):
                    response = session.get(url, allow_redirects=True)
            except Exception:
                return response, switched_host, url
            switched_host = (
                _retarget_session_host(session, getattr(response, "url", "") or url)
                or switched_host
            )
            landing = str(getattr(response, "url", "") or url)
            blocked = landing if _url_is_auth(landing) else None
            return response, switched_host, blocked
        if _is_foreign_host(url, origin_host):
            response = _foreign_hop_get(session, url, origin_host)
        else:
            response = session.get(url, allow_redirects=False)
        switched_host = (
            _retarget_session_host(session, getattr(response, "url", "") or "")
            or switched_host
        )
        location = _redirect_location(response)
        if location:
            if _is_foreign_login(location, origin_host):
                switched_host = (
                    _retarget_session_host(session, location) or switched_host
                )
                return response, switched_host, location
            switched_host = _retarget_session_host(session, location) or switched_host
            url = location
            continue
        current_url = str(getattr(response, "url", "") or url)
        if _url_is_login(current_url):
            blocked = (
                current_url if _is_foreign_login(current_url, origin_host) else None
            )
            return response, switched_host, blocked
        if _url_is_auth(current_url) and not _is_foreign_host(current_url, origin_host):
            switched_host = (
                _retarget_session_host(session, current_url) or switched_host
            )
            response = session.get(current_url, allow_redirects=True)
            switched_host = (
                _retarget_session_host(
                    session, getattr(response, "url", "") or current_url
                )
                or switched_host
            )
        break
    return response, switched_host, None


@mcp.tool()
def get_courses() -> list[dict[str, Any]]:
    """
    Retrieve all available courses with their teachers.

    Returns:
        List of courses with name and teacher information.
    """
    try:
        courses_list = []

        for course in Courses(_session()):
            teacher_names = _safe_get_teacher_names(course.teachers, use_last_name=True)
            courses_list.append(
                {
                    "name": course.name,
                    "teachers": teacher_names,
                }
            )

        return courses_list

    except Exception as e:
        return [{"error": _tool_error(e, "Failed to retrieve courses")}]


def _install_lenient_grade_colors() -> None:
    """Accept grade colors the pinned library enum does not list.

    De Pass sends ``blue``. This runs at import so ``PercentageGraphic``
    validation in this process accepts that color before ``get_results``.
    """
    from smartschool_mcp.graphic_color import relax_graphic_colors

    relax_graphic_colors()


_install_lenient_grade_colors()


_DOCUMENTS_BROWSE_RE = re.compile(
    r"^/Documents/Index/Index/courseID/(\d+)(?:/parentID/\d+)?/ssID/(\d+)$"
)
_DOCUMENTS_DOWNLOAD_RE = re.compile(
    r"^/Documents/Download/Index/htm/\d/courseID/(\d+)/docID/(\d+)/ssID/(\d+)$"
)


def _course_document_item(item: object) -> dict[str, Any]:
    name = str(getattr(item, "name", "") or "")
    link = getattr(item, "link", None)
    if isinstance(link, str) and link.strip():
        return {"kind": "link", "name": name, "link": link.strip()}
    browse = getattr(item, "browse_url", None)
    download = getattr(item, "download_url", None)
    if (
        isinstance(browse, str)
        and browse.startswith("/Documents/")
        and not isinstance(download, str)
    ):
        return {"kind": "folder", "name": name, "browse_url": browse}
    modified = _safe_format_date(getattr(item, "last_modified", None))
    if modified is None:
        raw_modified = getattr(item, "last_modified", None)
        modified = raw_modified if isinstance(raw_modified, str) else None
    size_kb = getattr(item, "size_kb", None)
    if not isinstance(size_kb, (int, float)) or isinstance(size_kb, bool):
        size_kb = None
    view_url = getattr(item, "view_url", None)
    doc_id = getattr(item, "id", None)
    return {
        "kind": "file",
        "id": doc_id if isinstance(doc_id, int) else None,
        "name": name,
        "mime_type": str(getattr(item, "mime_type", "") or ""),
        "size_kb": size_kb,
        "last_modified": modified,
        "view_url": view_url if isinstance(view_url, str) else None,
    }


def _checked_browse_url(
    browse_url: str | None, course_id: int, platform_id: int
) -> str | None:
    if browse_url is None:
        return None
    match = _DOCUMENTS_BROWSE_RE.fullmatch(browse_url.strip())
    if match is None:
        raise ValueError("browse_url is not a course Documents folder path")
    if int(match.group(1)) != course_id or int(match.group(2)) != platform_id:
        raise ValueError("browse_url does not match course_id and platform_id")
    return browse_url.strip()


@mcp.tool()
def get_course_documents(
    course_id: int | None = None,
    platform_id: int | None = None,
    browse_url: str | None = None,
) -> dict[str, Any]:
    """
    List Documenten for one course in the lesson module (Vakken).

    Without course_id, returns TopNav courses (integer id + platform_id).
    Those ids are not the ids from get_courses (results API).

    With course_id, lists one folder via FolderItem:
    ``GET /Documents/Index/Index/courseID/{courseId}/ssID/{platformId}``.
    That HTML listing and its parentID subfolders are in the library fixtures
    and were seen live on De Ring. Pass browse_url from a folder row to open
    a subfolder. Uploadzone (indienzone) is a different module and is not listed.

    Args:
        course_id: TopNav course id. Omit to list courses.
        platform_id: School platform id. Looked up from TopNav when omitted.
        browse_url: Optional Documents folder path returned by an earlier call.
    """
    try:
        session = _session()
        if course_id is None:
            courses = []
            for course in TopNavCourses(session):
                courses.append(
                    {
                        "id": getattr(course, "id", None),
                        "platform_id": getattr(course, "platform_id", None),
                        "name": getattr(course, "name", "") or "",
                        "teacher": getattr(course, "teacher", "") or "",
                    }
                )
            return {
                "courses": courses,
                "total": len(courses),
                "note": (
                    "course_id is the TopNav Documents id, not the id from get_courses"
                ),
            }
        if (
            isinstance(course_id, bool)
            or not isinstance(course_id, int)
            or course_id <= 0
        ):
            return {"error": "Invalid course_id"}

        resolved_platform = platform_id
        course_name = ""
        teacher = ""
        if resolved_platform is None:
            for course in TopNavCourses(session):
                if getattr(course, "id", None) == course_id:
                    resolved_platform = getattr(course, "platform_id", None)
                    course_name = getattr(course, "name", "") or ""
                    teacher = getattr(course, "teacher", "") or ""
                    break
        if (
            isinstance(resolved_platform, bool)
            or not isinstance(resolved_platform, int)
            or resolved_platform <= 0
        ):
            return {"error": "platform_id is required and was not found on TopNav"}

        safe_browse = _checked_browse_url(browse_url, course_id, resolved_platform)
        course = CourseCondensed(
            session=session,
            name=course_name or str(course_id),
            teacher=teacher,
            url="",
            id=course_id,
            platform_id=resolved_platform,
        )
        folder = FolderItem(
            session=session,
            parent=None,
            course=course,
            name="(Root)",
            browse_url=safe_browse,
        )
        items = [_course_document_item(item) for item in folder.items]
        return {
            "course_id": course_id,
            "platform_id": resolved_platform,
            "browse_url": folder.browse_url,
            "items": items,
            "total": len(items),
        }
    except Exception as e:
        return {"error": _tool_error(e, "Failed to retrieve course documents")}


@mcp.tool()
def download_course_document(
    course_id: int,
    document_id: int,
    platform_id: int | None = None,
    browse_url: str | None = None,
    save_path: str | None = None,
) -> dict[str, Any]:
    """
    Download one file from a course Documenten folder.

    The file is looked up in that folder (not in subfolders) and saved from
    the download path FolderItem parsed out of the HTML, which in the library
    fixtures is ``/Documents/Download/Index/htm/{0|1}/courseID/.../docID/...``.

    Args:
        course_id: TopNav course id from get_course_documents.
        document_id: File id from a ``kind=file`` row.
        platform_id: School platform id. Looked up when omitted.
        browse_url: Subfolder path when the file is not in the course root.
        save_path: Optional directory (default: ~/Downloads/smartschool/).
    """
    try:
        if (
            isinstance(course_id, bool)
            or not isinstance(course_id, int)
            or course_id <= 0
        ):
            return {"error": "Invalid course_id"}
        if (
            isinstance(document_id, bool)
            or not isinstance(document_id, int)
            or document_id <= 0
        ):
            return {"error": "Invalid document_id"}
        listing = get_course_documents(
            course_id=course_id, platform_id=platform_id, browse_url=browse_url
        )
        if "error" in listing:
            return {"error": str(listing["error"])}
        match = next(
            (
                item
                for item in listing.get("items") or []
                if item.get("kind") == "file" and item.get("id") == document_id
            ),
            None,
        )
        if match is None:
            link = next(
                (
                    item
                    for item in listing.get("items") or []
                    if item.get("kind") == "link" and item.get("id") == document_id
                ),
                None,
            )
            if link:
                return {
                    "error": "That row is a link, not a file",
                    "link": link.get("link"),
                }
            return {
                "error": (
                    f"Document {document_id} not found in this folder. "
                    "Pass browse_url to look inside a subfolder."
                )
            }

        session = _session()
        resolved_platform = listing["platform_id"]
        safe_browse = _checked_browse_url(browse_url, course_id, resolved_platform)
        course = CourseCondensed(
            session=session,
            name=str(course_id),
            teacher="",
            url="",
            id=course_id,
            platform_id=resolved_platform,
        )
        folder = FolderItem(
            session=session,
            parent=None,
            course=course,
            name="(Root)",
            browse_url=safe_browse,
        )
        download_url = None
        filename = match.get("name") or f"document_{document_id}"
        for item in folder.items:
            if getattr(item, "id", None) != document_id:
                continue
            link = getattr(item, "link", None)
            if isinstance(link, str):
                return {
                    "error": "That row is a link, not a file",
                    "link": link,
                }
            download_url = getattr(item, "download_url", None)
            filename = getattr(item, "name", None) or filename
            break
        if (
            not isinstance(download_url, str)
            or _DOCUMENTS_DOWNLOAD_RE.fullmatch(download_url) is None
        ):
            return {"error": "Document has no Documents download path"}
        parsed = _DOCUMENTS_DOWNLOAD_RE.fullmatch(download_url)
        assert parsed is not None
        if (
            int(parsed.group(1)) != course_id
            or int(parsed.group(3)) != resolved_platform
        ):
            return {"error": "Refusing a download path for a different course"}
        if int(parsed.group(2)) != document_id:
            return {"error": "Refusing a download path for a different document"}
        resp = session.get(download_url)
        if not getattr(resp, "ok", False):
            status = getattr(resp, "status_code", "?")
            return {"error": f"Download failed: HTTP {status}"}
        saved_name = filename_from_response(
            str(filename),
            str(match.get("mime_type") or ""),
            resp,
        )
        saved = _write_download(resp.content, saved_name, save_path)
        saved.update(
            {
                "course_id": course_id,
                "document_id": document_id,
                "mime_type": match.get("mime_type") or "",
            }
        )
        return saved
    except Exception as e:
        return {"error": _tool_error(e, "Failed to download course document")}


_EVALUATION_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")


def _fetch_evaluation(result: object) -> dict[str, Any] | None:
    """Raw evaluation JSON, not the library model (tendencies are typed as strings)."""
    identifier = getattr(result, "identifier", None)
    if (
        not isinstance(identifier, str)
        or _EVALUATION_ID_RE.fullmatch(identifier) is None
    ):
        return None
    session = getattr(result, "session", None)
    json_call = getattr(session, "json", None)
    if not callable(json_call):
        return None
    try:
        payload = json_call(f"/results/api/v1/evaluations/{identifier}")
    except Exception:
        return None
    if isinstance(payload, dict):
        return payload
    return None


@mcp.tool()
def get_results(
    limit: int = 15,
    offset: int = 0,
    course_filter: str | None = None,
    include_details: bool = True,
    include_raw: bool = False,
) -> dict[str, Any]:
    """
    Retrieve student results/grades with detailed information.

    Class average and median come from the evaluation detail payload
    (``centralTendencies``, or a field such as ``classAverage`` /
    ``gemiddelde`` / ``mediaan`` when that is what the school sends).
    A school or teacher can hide those numbers. They stay null when the
    payload does not include them.

    Args:
        limit: Maximum number of results to return (default: 15)
        offset: Number of results to skip from the beginning (default: 0)
        course_filter: Filter results by course name (partial match, case-insensitive)
        include_details: Whether to fetch class average and median.
            Saves API calls if False. The teacher on each row comes from
            the list payload either way.
        include_raw: On the first result only, add a secret-free summary of
            the detail JSON: key names and whether score-like average/median
            values were present. No names and no cookies.

    Returns:
        Dictionary with results list and pagination info.

    Examples:
        - get_results() -> First 15 results with details
        - get_results(course_filter="Math") -> Results from courses containing "Math"
        - get_results(include_details=False) -> Basic info only, faster response
        - get_results(include_raw=True) -> First row also has a ``raw`` diagnostic
    """
    try:
        results = Results(_session())
        all_results = list(results)

        # Apply course filtering
        if course_filter:
            filtered = []
            for result in all_results:
                course_name = result.courses[0].name if result.courses else ""
                if course_filter.lower() in course_name.lower():
                    filtered.append(result)
            all_results = filtered

        # Apply pagination
        end_index = offset + limit
        paginated = all_results[offset:end_index]

        results_list: list[dict[str, Any]] = []

        for result in paginated:
            # Basic result information (teacher comes from gradebook_owner,
            # no extra API call)
            graphic = result.graphic
            result_data = {
                "course": result.courses[0].name if result.courses else "Unknown",
                "assignment": result.name or "Unknown Assignment",
                "teacher": result.gradebook_owner.name.starting_with_first_name,
                "period": result.period.name if result.period else None,
                "score_description": getattr(graphic, "description", "N/A"),
                "score_value": getattr(graphic, "value", None),
                "achieved_points": getattr(graphic, "achieved_points", None),
                "total_points": getattr(graphic, "total_points", None),
                "percentage": getattr(graphic, "percentage", None),
                "date": _safe_format_date(result.date),
                "published_date": _safe_format_date(result.availability_date),
                "counts": result.does_count,
                "feedback": result.feedback[0].text if result.feedback else "",
            }

            # Class average/median only when requested. The list payload has
            # the teacher already; these numbers live on the detail call.
            first_row = not results_list
            raw_payload = None
            if include_details or (include_raw and first_row):
                raw_payload = _fetch_evaluation(result)
            if include_details:
                result_data.update({"average": None, "median": None})
                if raw_payload is not None:
                    average, median = statistics_from_payload(raw_payload)
                    result_data["average"] = average
                    result_data["median"] = median
                else:
                    try:
                        detail = result.details
                    except Exception:
                        detail = None
                    if detail is not None:
                        average, median = statistics_from_detail(detail)
                        result_data["average"] = average
                        result_data["median"] = median
            if include_raw and first_row:
                result_data["raw"] = detail_diagnostic(raw_payload)

            results_list.append(result_data)

        total_results = len(all_results)
        return {
            "results": results_list,
            "pagination": {
                "limit": limit,
                "offset": offset,
                "total": total_results,
                "returned": len(results_list),
                "has_more": end_index < total_results,
            },
            "filters": {
                "course_filter": course_filter,
                "include_details": include_details,
            },
        }

    except Exception as e:
        return {"error": _tool_error(e, "Failed to retrieve results")}


class _TaskDict(TypedDict):
    label: str
    description: str
    warning: bool


class _CourseDict(TypedDict):
    name: str
    tasks: list[_TaskDict]


class _DayDict(TypedDict):
    date: str | None
    courses: list[_CourseDict]


# Legacy Agenda future-tasks returns every upcoming row. Planner needs a window;
# a year covers the rest of the school year without an open-ended query.
_FUTURE_TASKS_HORIZON_DAYS = 366
_FUTURE_TASK_PLANNER_TYPES = "planned-assignments"


def _agenda_task_days(future_tasks: Any) -> list[_DayDict]:
    """Map ``FutureTasks`` (old Schoolagenda) into the tool's day/course shape."""
    tasks_data: list[_DayDict] = []
    for day in future_tasks:
        day_data: _DayDict = {
            "date": _safe_format_date(day.date),
            "courses": [],
        }

        for course in day.courses:
            course_data: _CourseDict = {
                "name": course.course_title,
                "tasks": [],
            }

            if hasattr(course, "items") and hasattr(course.items, "tasks"):
                for task in course.items.tasks:
                    task_data: _TaskDict = {
                        "label": getattr(task, "label", "N/A"),
                        "description": getattr(task, "description", "N/A"),
                        "warning": getattr(task, "warning", False),
                    }
                    course_data["tasks"].append(task_data)

            if course_data["tasks"]:
                day_data["courses"].append(course_data)

        if day_data["courses"]:
            tasks_data.append(day_data)
    return tasks_data


def _planned_course_names(element: object) -> str:
    courses = getattr(element, "courses", None) or []
    names: list[str] = []
    for course in courses:
        name = getattr(course, "name", None)
        if isinstance(name, str) and name.strip():
            names.append(name.strip())
    return ", ".join(names) if names else "Unknown"


def _planned_element_day(element: object) -> date | None:
    period = getattr(element, "period", None)
    start = getattr(period, "date_time_from", None) if period is not None else None
    if isinstance(start, datetime):
        return start.date()
    if isinstance(start, date):
        return start
    return None


def _planner_assignment_days(session: Smartschool) -> list[_DayDict]:
    """Map Planner ``planned-assignments`` onto the future-tasks shape.

    Used when the legacy Agenda list is empty. De Pass stores Toets and
    Huistaak items on the calendar, not in ``/Agenda/Futuretasks``.
    """
    start = date.today()
    end = start + timedelta(days=_FUTURE_TASKS_HORIZON_DAYS)
    by_day: dict[str, dict[str, _CourseDict]] = {}

    for element in PlannedElements(
        session,
        from_date=start,
        till_date=end,
        types=_FUTURE_TASK_PLANNER_TYPES,
    ):
        element_type = getattr(element, "planned_element_type", None)
        if element_type not in (None, _FUTURE_TASK_PLANNER_TYPES):
            continue
        day = _planned_element_day(element)
        if day is None or day < start:
            continue

        course_name = _planned_course_names(element)

        assignment_type = getattr(element, "assignment_type", None)
        raw_label = (
            getattr(assignment_type, "name", None)
            if assignment_type is not None
            else None
        )
        label = (
            raw_label.strip()
            if isinstance(raw_label, str) and raw_label.strip()
            else "Assignment"
        )
        raw_description = getattr(element, "name", None)
        description = (
            raw_description.strip()
            if isinstance(raw_description, str) and raw_description.strip()
            else "N/A"
        )
        warning = getattr(element, "warning", False)
        task: _TaskDict = {
            "label": label,
            "description": description,
            "warning": warning if isinstance(warning, bool) else False,
        }

        day_key = day.strftime("%Y-%m-%d")
        courses_for_day = by_day.setdefault(day_key, {})
        course_data = courses_for_day.get(course_name)
        if course_data is None:
            course_data = {"name": course_name, "tasks": []}
            courses_for_day[course_name] = course_data
        course_data["tasks"].append(task)

    return [
        {"date": day_key, "courses": list(courses_for_day.values())}
        for day_key, courses_for_day in by_day.items()
    ]


def _future_tasks_payload(tasks_data: list[_DayDict]) -> dict[str, Any]:
    total_tasks = sum(
        len(course["tasks"]) for day in tasks_data for course in day["courses"]
    )
    return {
        "future_tasks": tasks_data,
        "total_days": len(tasks_data),
        "total_tasks": total_tasks,
    }


@mcp.tool()
def get_future_tasks() -> dict[str, Any]:
    """
    Retrieve upcoming assignments and tasks.

    The legacy Schoolagenda call (POST /Agenda/Futuretasks/getFuturetasks)
    is tried first. On Planner schools such as De Pass that list is empty
    while tests and homework are calendar rows of type planned-assignments.
    An empty or failing legacy list falls back to those Planner rows from
    today through the next 366 days, in the same date/course/task shape.

    For the full calendar (lessons, activities, other types) use
    get_planned_elements.

    Returns:
        Dictionary with future tasks organized by date and course.
    """
    agenda_error: Exception | None = None
    try:
        tasks_data = _agenda_task_days(FutureTasks(_session()))
    except Exception as exc:
        agenda_error = exc
        tasks_data = []

    if tasks_data:
        return _future_tasks_payload(tasks_data)

    try:
        planner_days = _planner_assignment_days(_session())
    except Exception:
        # A successful empty Agenda list stays empty when Planner is unavailable.
        # An Agenda failure is reported only when Planner fails too.
        if agenda_error is None:
            return _future_tasks_payload([])
        return {"error": _tool_error(agenda_error, "Failed to retrieve future tasks")}

    return _future_tasks_payload(planner_days)


@mcp.tool()
def get_messages(
    limit: int = 15,
    offset: int = 0,
    box_type: str = "INBOX",
    search_query: str | None = None,
    sender_filter: str | None = None,
    include_body: bool = False,
) -> dict[str, Any]:
    """
    Retrieve messages from the specified mailbox with filtering options.

    Args:
        limit: Maximum number of messages to return (default: 15)
        offset: Number of messages to skip from the beginning (default: 0)
        box_type: Type of mailbox - "INBOX", "SENT", "DRAFT", "SCHEDULED",
            "TRASH" (default: "INBOX")
        search_query: Search in subject and body content (case-insensitive)
        sender_filter: Filter messages by sender name (partial match,
            case-insensitive)
        include_body: Whether to include full message body
            (default: False for performance)

    Returns:
        Dictionary with messages list and pagination info.

    Examples:
        - get_messages() -> First 15 inbox messages (headers only)
        - get_messages(search_query="homework") -> Messages containing "homework"
        - get_messages(sender_filter="teacher") -> Messages from senders containing
          "teacher"
        - get_messages(include_body=True) -> Full messages with body content
    """
    try:
        # Convert string box_type to BoxType enum
        try:
            box_type_enum = getattr(BoxType, box_type.upper())
        except AttributeError:
            box_type_enum = BoxType.INBOX  # Default fallback

        # Get message headers — headers already carry from_, subject, date, unread
        all_headers = list(MessageHeaders(_session(), box_type=box_type_enum))  # type: ignore[abstract, var-annotated]

        # Apply sender filter directly from headers (no full message fetch needed)
        if sender_filter:
            all_headers = [
                h
                for h in all_headers
                if sender_filter.lower() in (getattr(h, "from_", "") or "").lower()
            ]

        # Apply search query: check subject first, only fetch body when subject
        # doesn't match
        if search_query:
            matched = []
            for header in all_headers:
                subject = (getattr(header, "subject", "") or "").lower()
                if search_query.lower() in subject:
                    matched.append(header)
                    continue
                # Fall back to fetching full message body
                try:
                    full_msg = Message(_session(), header.id).get()  # type: ignore[abstract, var-annotated]
                    body = (getattr(full_msg, "body", "") or "").lower()
                    if search_query.lower() in body:
                        header._cached_message = full_msg
                        matched.append(header)
                except Exception:
                    continue
            all_headers = matched

        # Apply pagination
        end_index = offset + limit
        paginated = all_headers[offset:end_index]

        messages_list = []

        for header in paginated:
            attachment_count = (
                getattr(header, "attachments", None)
                or getattr(header, "attachment", 0)
                or 0
            )
            message_data = {
                "id": getattr(header, "id", None),
                "from": getattr(header, "from_", "Unknown Sender"),
                "subject": getattr(header, "subject", "No Subject"),
                "date": _safe_format_date(getattr(header, "date", None)),
                "unread": getattr(header, "unread", None),
                "priority": getattr(header, "priority", None),
                "has_attachments": attachment_count > 0,
                "attachment_count": attachment_count,
            }

            # Include body if explicitly requested or already cached from search
            cached = getattr(header, "_cached_message", None)
            if include_body:
                try:
                    full_msg = cached or Message(_session(), header.id).get()  # type: ignore[abstract]
                    message_data["body"] = getattr(full_msg, "body", "")
                except Exception:
                    message_data["body"] = ""
            else:
                if cached:
                    body = getattr(cached, "body", "") or ""
                    message_data["body_preview"] = (
                        body[:100] + "..." if len(body) > 100 else body
                    )
                else:
                    message_data["body_preview"] = None

            messages_list.append(message_data)

        total_messages = len(all_headers)
        return {
            "messages": messages_list,
            "pagination": {
                "limit": limit,
                "offset": offset,
                "total": total_messages,
                "returned": len(messages_list),
                "has_more": end_index < total_messages,
            },
            "filters": {
                "box_type": box_type,
                "search_query": search_query,
                "sender_filter": sender_filter,
                "include_body": include_body,
            },
        }

    except Exception as e:
        return {"error": _tool_error(e, "Failed to retrieve messages")}


@mcp.tool()
def get_schedule(date_offset: int = 0, includes: str | None = None) -> dict[str, Any]:
    """
    Retrieve the Planner calendar for a given day.

    Same portal call as the website timetable: GET
    /planner/api/v1/planned-elements/user/{id} with from/to only (no types
    filter). Schoolagenda XML is not used.

    Args:
        date_offset: Days from today (0=today, 1=tomorrow, -1=yesterday,
            default: 0)
        includes: Optional comma-separated expansions the SPA uses
            (e.g. icon,courses,locations,upload-folders,labels)

    Returns:
        Dictionary with planned elements for the given date. Each element
        includes ``id`` and ``description`` (empty when the portal omits it)
        and ``upload_folders`` when the response contains them. Pass
        ``includes`` with ``upload-folders`` to ask for attachments. Whether
        that include adds ``uploadFolders`` is not live-verified.
    """
    try:
        target_date = date.today() + timedelta(days=date_offset)
        elements_list = fetch_calendar(
            _session(),
            target_date,
            target_date,
            None,
            _csv_or_none(includes),
        )

        return {
            "date": target_date.strftime("%Y-%m-%d"),
            "elements": elements_list,
            "total": len(elements_list),
        }

    except Exception as e:
        return {"error": _tool_error(e, "Failed to retrieve schedule")}


@mcp.tool()
def get_periods() -> list[dict[str, Any]]:
    """
    Retrieve academic periods/terms for the current school year.

    Returns:
        List of academic periods with name, dates, and active status.
    """
    try:
        periods_list = []

        for period in Periods(_session()):
            periods_list.append(
                {
                    "name": period.name,
                    "is_active": period.is_active,
                    "class": period.class_.name if period.class_ else None,
                    "school_year_start": _safe_format_date(
                        period.skore_work_year.date_range.start
                    ),
                    "school_year_end": _safe_format_date(
                        period.skore_work_year.date_range.end
                    ),
                }
            )

        return periods_list

    except Exception as e:
        return [{"error": _tool_error(e, "Failed to retrieve periods")}]


@mcp.tool()
def get_reports() -> list[dict[str, Any]]:
    """
    Retrieve available academic report cards.

    Returns:
        List of report cards with name, date, class, and school year label.
    """
    try:
        reports_list = []

        for report in Reports(_session()):
            reports_list.append(
                {
                    "name": report.name,
                    "date": _safe_format_date(report.date),
                    "class": report.class_.name if report.class_ else None,
                    "schoolyear_label": report.schoolyear_label,
                }
            )

        return reports_list

    except Exception as e:
        return [{"error": _tool_error(e, "Failed to retrieve reports")}]


@mcp.tool()
def get_planned_elements(
    days_ahead: int = 34,
    from_date: str | None = None,
    to_date: str | None = None,
    types: str | None = None,
    includes: str | None = None,
) -> dict[str, Any]:
    """
    Retrieve planned elements from the Smartschool planner.

    Default matches the website calendar: from/to only, no types filter, so
    lessons, activities, placeholders, assignments, and to-dos are included.
    Pass types for a sidebar subset (comma-separated planned-* values).

    Args:
        days_ahead: Number of days ahead when from_date/to_date are omitted
            (default: 34)
        from_date: Inclusive start date YYYY-MM-DD (default: today)
        to_date: Inclusive end date YYYY-MM-DD (default: from_date + days_ahead)
        types: Optional comma-separated plannedElementType filter
        includes: Optional comma-separated expansions (icon,courses,locations,…)

    Returns:
        Dictionary with planned elements including id, description, dates,
        courses, assignment types, and upload folders when the payload has them.
    """
    try:
        start = date.fromisoformat(from_date) if from_date else date.today()
        end = (
            date.fromisoformat(to_date)
            if to_date
            else start + timedelta(days=days_ahead)
        )
        elements_list = fetch_calendar(
            _session(),
            start,
            end,
            _csv_or_none(types),
            _csv_or_none(includes),
        )

        return {
            "planned_elements": elements_list,
            "total": len(elements_list),
            "period": {
                "from": start.strftime("%Y-%m-%d"),
                "to": end.strftime("%Y-%m-%d"),
            },
        }

    except Exception as e:
        return {"error": _tool_error(e, "Failed to retrieve planned elements")}


def _planner_window(
    from_date: str | None, to_date: str | None, days_ahead: int
) -> tuple[date, date]:
    start = date.fromisoformat(from_date) if from_date else date.today()
    end = date.fromisoformat(to_date) if to_date else start + timedelta(days=days_ahead)
    return start, end


def _merge_planner_detail(
    row: dict[str, Any], detail: dict[str, Any] | None
) -> dict[str, Any]:
    if not detail:
        return row
    merged = dict(row)
    if not merged.get("description") and detail.get("description"):
        merged["description"] = detail["description"]
    if not merged.get("upload_folders") and detail.get("upload_folders"):
        merged["upload_folders"] = detail["upload_folders"]
    return merged


@mcp.tool()
def get_planner_attachments(
    element_id: str,
    from_date: str | None = None,
    to_date: str | None = None,
    days_ahead: int = 34,
    include_raw: bool = False,
) -> dict[str, Any]:
    """
    List upload folders and files attached to one planner item.

    Refetches the calendar with the website include list
    ``icon,courses,locations,upload-folders,labels`` and returns the matching
    element's id, description, and upload folders.

    The captured library fixtures do not contain those fields. Key names
    (``uploadFolders``, ``description``, ``publicInfo``) are inferred, not
    live-verified. When the calendar row is an assignment and still has no
    description and no folders, this also tries
    ``GET /planner/api/v1/planned-assignments/{platformId}/{assignmentId}``.
    That path is catalogued; its body is not.

    Args:
        element_id: Planner element UUID from get_schedule or get_planned_elements.
        from_date: Inclusive start YYYY-MM-DD (default: today).
        to_date: Inclusive end YYYY-MM-DD (default: from_date + days_ahead).
        days_ahead: Used when to_date is omitted (default: 34).
        include_raw: Add key names and URL-like fields from the calendar row
            and, when the school sends one, the assignment-detail JSON.
            Query strings, cookies, and secret-looking values are left out.
            Use this when ``download_planner_file`` cannot find a path.
    """
    try:
        safe_id = element_id_or_none(element_id)
        if safe_id is None:
            return {"error": "Invalid element_id"}
        start, end = _planner_window(from_date, to_date, days_ahead)
        session = _session()
        raw_rows = fetch_calendar_raw(
            session, start, end, None, PLANNER_ATTACHMENT_INCLUDES
        )
        raw_row = next(
            (
                item
                for item in raw_rows
                if str(item.get("id") or "").lower() == safe_id.lower()
            ),
            None,
        )
        if raw_row is None:
            return {
                "error": (
                    f"Planner element {safe_id} not found between "
                    f"{start.isoformat()} and {end.isoformat()}"
                )
            }
        row = calendar_element_dict(raw_row)

        detail_status = "not_called"
        element_type = row.get("type")
        needs_detail = element_type in ASSIGNMENT_DETAIL_TYPES and (
            not row.get("description") and not row.get("upload_folders")
        )
        platform_id = row.get("platform_id")
        detail_raw: dict[str, Any] | None = None
        if needs_detail and isinstance(platform_id, int):
            detail_status = "unavailable"
            detail = fetch_assignment_detail(session, platform_id, safe_id)
            if detail is not None:
                detail_status = "parsed"
                row = _merge_planner_detail(row, detail)
        if (
            include_raw
            and element_type in ASSIGNMENT_DETAIL_TYPES
            and isinstance(platform_id, int)
            and not isinstance(platform_id, bool)
        ):
            try:
                loaded = session.json(
                    f"/planner/api/v1/planned-assignments/{platform_id}/{safe_id}"
                )
            except Exception:
                loaded = None
            if isinstance(loaded, dict):
                detail_raw = loaded

        payload: dict[str, Any] = {
            "element_id": safe_id,
            "name": row.get("name") or "",
            "type": element_type,
            "description": row.get("description") or "",
            "upload_folders": row.get("upload_folders") or [],
            "period": {"from": start.isoformat(), "to": end.isoformat()},
            "live_verified": False,
            "detail_fetch": {
                "path": (
                    "/planner/api/v1/planned-assignments/{platformId}/{assignmentId}"
                ),
                "status": detail_status,
                "body_verified": False,
            },
        }
        if include_raw:
            payload["raw"] = {
                "attachment": attachment_diagnostic(raw_row),
                "detail": (
                    json_diagnostic(detail_raw)
                    if detail_raw is not None
                    else {"present": False, "keys": [], "url_fields": []}
                ),
            }
        return payload
    except Exception as e:
        return {"error": _tool_error(e, "Failed to retrieve planner attachments")}


_PLANNER_DOWNLOAD_FAILED = (
    "Dit plannerbestand heeft geen werkend downloadpad. "
    "Er stond geen link van de school in de gegevens, "
    "en de paden die we daarna probeerden gaven geen bestand terug. "
    "Vraag de bijlagen opnieuw op met include_raw op true "
    "en kijk welke veldnamen en links erin staan."
)


def _reported_download_path(target: str) -> str:
    return target.split("?", 1)[0].split("#", 1)[0]


def _response_is_download(resp: object) -> bool:
    """True for a file body. HTML and JSON error envelopes are not files."""
    if not getattr(resp, "ok", False):
        return False
    content = getattr(resp, "content", b"")
    if not isinstance(content, (bytes, bytearray)) or not content:
        return False
    headers = getattr(resp, "headers", None)
    content_type = (header_value(headers, "Content-Type") or "").split(";", 1)[0]
    content_type = content_type.strip().lower()
    if content_type in {"text/html", "application/xhtml+xml"}:
        return False
    head = bytes(content[:300]).lstrip().lower()
    if head.startswith((b"<!doctype", b"<html", b"<head")):
        return False
    disposition = header_value(headers, "Content-Disposition") or ""
    unnamed_json = (
        content_type == "application/json" and "filename" not in disposition.lower()
    )
    return not unnamed_json


@mcp.tool()
def download_planner_file(
    element_id: str,
    file_id: str,
    from_date: str | None = None,
    to_date: str | None = None,
    days_ahead: int = 34,
    save_path: str | None = None,
) -> dict[str, Any]:
    """
    Download one file from a planner item's upload folders.

    A same-host URL in the calendar or assignment-detail payload is tried
    first. If that is missing or does not return a file, a few planner-shaped
    paths built from the file id are tried with GET, still on the same host,
    and the first one that returns a file is saved. ``download_path`` says
    which path worked. ``live_verified`` stays false: none of those paths is
    confirmed against the website.

    If every path fails, the error is in Dutch and lists the paths that were
    tried. Ask for ``get_planner_attachments(..., include_raw=true)`` to see
    the field names and links the school actually sent.

    Args:
        element_id: Planner element UUID.
        file_id: File id from get_planner_attachments.
        from_date: Inclusive start YYYY-MM-DD (default: today).
        to_date: Inclusive end YYYY-MM-DD (default: from_date + days_ahead).
        days_ahead: Used when to_date is omitted (default: 34).
        save_path: Optional directory (default: ~/Downloads/smartschool/).
    """
    try:
        safe_id = element_id_or_none(element_id)
        if safe_id is None:
            return {"error": "Invalid element_id"}
        safe_file = file_id.strip()
        if not safe_file or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", safe_file):
            return {"error": "Invalid file_id"}
        start, end = _planner_window(from_date, to_date, days_ahead)
        session = _session()
        raw_rows = fetch_calendar_raw(
            session, start, end, None, PLANNER_ATTACHMENT_INCLUDES
        )
        element = next(
            (
                raw
                for raw in raw_rows
                if str(raw.get("id") or "").lower() == safe_id.lower()
            ),
            None,
        )
        platform_id = element.get("platformId") if isinstance(element, dict) else None
        element_type = (
            element.get("plannedElementType") if isinstance(element, dict) else None
        )
        found = file_download(raw_rows, safe_id, safe_file)
        if found is None and (
            isinstance(platform_id, int)
            and not isinstance(platform_id, bool)
            and element_type in ASSIGNMENT_DETAIL_TYPES
        ):
            try:
                detail = session.json(
                    f"/planner/api/v1/planned-assignments/{platform_id}/{safe_id}"
                )
            except Exception:
                detail = None
            if isinstance(detail, dict):
                found = file_download([{**detail, "id": safe_id}], safe_id, safe_file)
        if found is None:
            return {
                "error": f"File {safe_file} not found on planner element {safe_id}",
                "live_verified": False,
            }
        candidates = planner_download_candidates(
            session,
            element_id=safe_id,
            file_info=found,
            platform_id=platform_id if isinstance(platform_id, int) else None,
        )
        tried: list[str] = []
        saved_response = None
        used_path = ""
        for target in candidates:
            reported = _reported_download_path(target)
            tried.append(reported)
            try:
                resp = session.get(target)
            except Exception:
                continue
            if _response_is_download(resp):
                saved_response = resp
                used_path = reported
                break
        if saved_response is None:
            return {
                "error": _PLANNER_DOWNLOAD_FAILED,
                "file_id": safe_file,
                "name": found.get("name") or "",
                "tried": tried,
                "live_verified": False,
            }
        saved_name = filename_from_response(
            str(found.get("name") or "") or f"planner_{safe_file}",
            str(found.get("mime_type") or ""),
            saved_response,
        )
        saved = _write_download(saved_response.content, saved_name, save_path)
        saved.update(
            {
                "element_id": safe_id,
                "file_id": safe_file,
                "mime_type": found.get("mime_type") or "",
                "download_path": used_path,
                "live_verified": False,
            }
        )
        return saved
    except Exception as e:
        return {"error": _tool_error(e, "Failed to download planner file")}


def _write_download(
    content: bytes, raw_name: str, save_path: str | None
) -> dict[str, Any]:
    from pathlib import Path

    download_dir = (
        Path(save_path) if save_path else Path.home() / "Downloads" / "smartschool"
    )
    download_dir.mkdir(parents=True, exist_ok=True)
    filename = Path(raw_name).name or "download"
    file_path = download_dir / filename
    counter = 1
    stem = file_path.stem
    suffix = file_path.suffix
    while file_path.exists():
        file_path = download_dir / f"{stem} ({counter}){suffix}"
        counter += 1
    file_path.write_bytes(content)
    return {
        "name": file_path.name,
        "saved_to": str(file_path),
        "bytes_written": len(content),
    }


@mcp.tool()
def get_student_support_links() -> list[dict[str, Any]]:
    """
    Retrieve student support links and resources.

    Returns:
        List of visible support links with name, description, and URL.
    """
    try:
        links_list = []

        for link in StudentSupportLinks(_session()):
            if link.is_visible:
                links_list.append(
                    {
                        "name": link.name,
                        "description": link.description,
                        "link": link.clean_link,
                    }
                )

        return links_list

    except Exception as e:
        return [{"error": _tool_error(e, "Failed to retrieve support links")}]


@mcp.tool()
def get_children() -> dict[str, Any]:
    """
    List children linked on Mijn kinderen for this parent/co-account.

    Same portal call as the website: POST /Studentcard/Student/getStudents
    with ``X-Requested-With: XMLHttpRequest`` (without that header the portal
    can return HTTP 500 HTML). The current child may have ``accountID`` 0;
    switch ids are then taken from the Mijn kinderen topnav
    (``/Studentcard/Chain/gotourl/accountID/{id}``).
    Use ``account_id`` with switch_child to change whose Planner, results,
    and messages the other tools return. One login does not merge every
    child automatically — the session stays on the currently selected child.

    Returns:
        Dictionary with ``children``, ``current`` (the active session user),
        and ``total``.
    """
    try:
        session = _session()
        session.ensure_authenticated()
        payload: Any = []
        try:
            payload = session.json(
                "/Studentcard/Student/getStudents",
                method="post",
                headers=_STUDENTCARD_XHR_HEADERS,
            )
        except Exception:
            payload = []

        children = [_person_dict(raw) for raw in _as_child_records(payload)]
        try:
            html = getattr(session.get("/Studentcard"), "text", "") or ""
            children = _merge_topnav_children(children, _children_from_topnav(html))
        except Exception:
            pass

        current = _current_user_dict(session)
        children = [_mark_current_child(child, current) for child in children]
        return {
            "children": children,
            "current": current,
            "total": len(children),
        }
    except Exception as e:
        return {"error": _tool_error(e, "Failed to retrieve children")}


@mcp.tool()
def switch_child(account_id: str) -> dict[str, Any]:
    """
    Switch the session to another linked child (Mijn kinderen).

    Same portal call as the website: GET
    /Studentcard/Chain/gotourl/accountID/{accountId}. A child on another
    platform (e.g. De Ring) 302s to a one-time ``/otp/...`` URL; that hop is
    followed without replaying the original host. After a successful switch,
    get_schedule / get_planned_elements / get_results follow the newly
    selected child. Get account_id from get_children.

    Args:
        account_id: Linked-account id from get_children (not the Planner
            user id). Digits, letters, underscore, and hyphen only.

    Returns:
        Dictionary with the new ``current`` user, optional ``switched_host``
        when the chain landed on another school platform, and ``ok``.
    """
    try:
        safe_id = _safe_account_id(account_id)
        if safe_id is None:
            return {"error": "Invalid account_id"}

        session = _session()
        session.ensure_authenticated()
        origin_host = urlparse(session.create_url("/")).netloc
        path = f"/Studentcard/Chain/gotourl/accountID/{safe_id}"
        response, switched_host, blocked_login = _follow_child_switch_redirects(
            session, path
        )
        if response is None and not blocked_login:
            return {"error": "Child switch failed: empty response"}
        status = getattr(response, "status_code", 0) if response is not None else 0
        if (
            response is not None
            and not blocked_login
            and status not in _REDIRECT_STATUSES
            and not getattr(response, "ok", True)
        ):
            return {"error": f"Child switch failed: HTTP {status}"}

        if response is not None:
            switched_host = _follow_switched_host(session, response) or switched_host
        landing = blocked_login or str(getattr(response, "url", "") or "")
        if blocked_login or _url_is_login(landing):
            if origin_host:
                _retarget_session_host(session, f"https://{origin_host}/")
            auth_kind = (
                "login page"
                if _url_is_login(blocked_login or landing)
                else "authentication page"
            )
            failed: dict[str, Any] = {
                "ok": False,
                "error": (
                    f"Child switch landed on the other school's {auth_kind}; "
                    "not submitting this account's password there"
                ),
                "account_id": safe_id,
            }
            if switched_host:
                failed["switched_host"] = switched_host
            return failed

        user = _authenticated_user_from_response(session, response)
        if user is None and not _url_is_auth(landing):
            user = _authenticated_user_from_response(session, session.get("/"))

        current = (
            _person_dict(user)
            if isinstance(user, dict)
            else _current_user_dict(session)
        )
        result: dict[str, Any] = {
            "ok": True,
            "account_id": safe_id,
            "current": current,
        }
        if switched_host:
            result["switched_host"] = switched_host
        return result
    except Exception as e:
        return {"error": _tool_error(e, "Failed to switch child")}


def _attachment_file_id(att: object) -> object | None:
    """Return an Attachment's file id.

    smartschool renamed ``Attachment.fileID`` to ``file_id`` in 271edcc (v0.8.0).
    Accept both so the tool works across library versions.
    """
    file_id = getattr(att, "file_id", None)
    if file_id is None:
        file_id = getattr(att, "fileID", None)
    return file_id


@mcp.tool()
def get_attachments(message_id: int) -> dict[str, Any]:
    """
    List all attachments for a specific message.

    Args:
        message_id: The ID of the message to get attachments for
            (from get_messages results).

    Returns:
        Dictionary with attachment list including file names, sizes, and IDs
        for downloading.

    Examples:
        - get_attachments(249184) -> List attachments for message 249184
    """
    try:
        raw_attachments = list(Attachments(_session(), msg_id=message_id))  # type: ignore[abstract, var-annotated]
        attachments_list = [
            {
                "file_id": _attachment_file_id(att),
                "name": getattr(att, "name", "Unknown"),
                "mime_type": getattr(att, "mime", "Unknown"),
                "size": getattr(att, "size", "Unknown"),
            }
            for att in raw_attachments
        ]
        return {
            "message_id": message_id,
            "attachments": attachments_list,
            "total": len(attachments_list),
        }
    except Exception as e:
        return {"error": _tool_error(e, "Failed to retrieve attachments")}


@mcp.tool()
def download_attachment(
    message_id: int,
    file_id: int,
    save_path: str | None = None,
) -> dict[str, Any]:
    """
    Download a specific attachment from a message.

    Files are saved to *save_path* when provided, otherwise to
    ~/Downloads/smartschool/.  The directory is created automatically.
    Existing files are never overwritten — a counter suffix is appended
    instead (e.g. ``report (1).pdf``).

    Args:
        message_id: The ID of the message containing the attachment.
        file_id: The file ID of the attachment to download (from get_attachments).
        save_path: Optional directory to save the file into.

    Returns:
        Dictionary with the saved file path, filename, mime type, and bytes written.

    Examples:
        - download_attachment(249184, 12345) -> Download to ~/Downloads/smartschool/
        - download_attachment(249184, 12345, "/tmp") -> Download to /tmp/
    """
    from pathlib import Path

    try:
        target_attachment = None
        for att in Attachments(_session(), msg_id=message_id):  # type: ignore[abstract, var-annotated]
            if str(_attachment_file_id(att)) == str(file_id):
                target_attachment = att
                break

        if target_attachment is None:
            return {"error": f"Attachment {file_id} not found in message {message_id}"}

        # The upstream library's Attachment.download() incorrectly base64-decodes the
        # response; Smartschool actually returns raw binary.  Call the session directly.
        resp = _session().get(
            f"/?module=Messages&file=download&fileID={file_id}&target=0"
        )
        if not resp.ok:
            return {"error": f"Download failed: HTTP {resp.status_code}"}

        download_dir = (
            Path(save_path) if save_path else Path.home() / "Downloads" / "smartschool"
        )
        download_dir.mkdir(parents=True, exist_ok=True)

        # Sanitise filename to prevent path-traversal attacks
        raw_name = getattr(target_attachment, "name", "") or ""
        filename = Path(raw_name).name or f"attachment_{file_id}"
        file_path = download_dir / filename

        # Never overwrite — append a counter suffix
        counter = 1
        stem = file_path.stem
        while file_path.exists():
            file_path = download_dir / f"{stem} ({counter}){file_path.suffix}"
            counter += 1

        file_path.write_bytes(resp.content)

        return {
            "file_id": file_id,
            "name": filename,
            "mime_type": getattr(target_attachment, "mime", "Unknown"),
            "size": getattr(target_attachment, "size", "Unknown"),
            "saved_to": str(file_path),
            "bytes_written": len(resp.content),
        }

    except Exception as e:
        return {"error": _tool_error(e, "Failed to download attachment")}


def _parse_html(response: Any) -> Any:
    """Parse an HTML response into a BeautifulSoup tree.

    ``smartschool.bs4_html`` is not exported by every release, so fall back to
    BeautifulSoup directly rather than failing at import time.
    """
    markup: Any
    if isinstance(response, (str, bytes)):
        markup = response
    else:
        markup = getattr(response, "text", response)
    try:
        from smartschool import bs4_html

        return bs4_html(markup)
    except Exception:
        from bs4 import BeautifulSoup

        return BeautifulSoup(markup, "html.parser")


def _absolutise(session: Smartschool, url: str) -> str:
    """Turn a site-relative asset path into an absolute URL."""
    if not url or url.startswith(("http://", "https://", "data:")):
        return url
    return session.create_url(url) if url.startswith("/") else url


@mcp.tool()
def get_homepage_blocks(include_html: bool = False) -> dict[str, Any]:
    """
    Retrieve the "in de kijker" blocks pinned to the Smartschool homepage.

    Schools use these blocks for recurring content that is never sent as a
    message and never lands in a document module — a monthly lunch menu
    ("Maandmenu"), a monthly calendar ("Maandkalender"), announcements. The
    content is frequently an embedded image rather than text, so ``images``
    is usually where the information actually lives.

    Args:
        include_html: Also return each block's raw inner HTML (default: False).

    Returns:
        Dictionary with the list of blocks. Each block has a title, its
        news_id, plain text, and absolute URLs for any embedded images and
        links.

    Examples:
        - get_homepage_blocks() -> [{"title": "Maandmenu", "images": [...]}, ...]
    """
    try:
        session = _session()
        html = _parse_html(session.get("/"))

        blocks = []
        for block in html.select("div.homepage__block"):
            title_el = block.select_one(".homepage__block__top__title")
            content = block.select_one(".homepage__block__content")
            if content is None:
                continue

            images = [
                _absolutise(session, img["src"])
                for img in content.select("img[src]")
                if img.get("src")
            ]
            links = [
                _absolutise(session, a["href"])
                for a in content.select("a[href]")
                if a.get("href")
            ]

            entry: dict[str, Any] = {
                "title": title_el.get_text(strip=True) if title_el else None,
                "news_id": block.get("newsid"),
                "modname": block.get("modname"),
                "text": " ".join(content.get_text(" ", strip=True).split()),
                "images": images,
                "links": links,
            }
            if include_html:
                entry["html"] = content.decode_contents()
            blocks.append(entry)

        return {"blocks": blocks, "total": len(blocks)}

    except Exception as e:
        return {"error": _tool_error(e, "Failed to retrieve homepage blocks")}


@mcp.tool()
def download_homepage_image(
    image_url: str,
    save_path: str | None = None,
) -> dict[str, Any]:
    """
    Download an image embedded in a homepage block.

    Use the URLs returned in ``get_homepage_blocks()["blocks"][*]["images"]``.
    Only assets on the configured Smartschool host are accepted.

    Args:
        image_url: Image URL from get_homepage_blocks.
        save_path: Optional directory to save into (default: ~/Downloads/smartschool/).

    Returns:
        Dictionary with the saved file path and bytes written.
    """
    from pathlib import Path
    from urllib.parse import unquote, urlparse

    try:
        session = _session()
        base = session.create_url("/")
        if not image_url.startswith(base):
            return {"error": f"Refusing to fetch a URL outside {base}"}

        resp = session.get(image_url)
        if not resp.ok:
            return {"error": f"Failed to download image: HTTP {resp.status_code}"}

        download_dir = (
            Path(save_path) if save_path else Path.home() / "Downloads" / "smartschool"
        )
        download_dir.mkdir(parents=True, exist_ok=True)

        # Sanitise filename to prevent path-traversal attacks
        filename = Path(unquote(urlparse(image_url).path)).name or "homepage_image"
        file_path = download_dir / filename

        # Never overwrite — append a counter suffix
        counter = 1
        stem = file_path.stem
        while file_path.exists():
            file_path = download_dir / f"{stem} ({counter}){file_path.suffix}"
            counter += 1

        file_path.write_bytes(resp.content)

        return {
            "name": file_path.name,
            "saved_to": str(file_path),
            "bytes_written": len(resp.content),
        }

    except Exception as e:
        return {"error": _tool_error(e, "Failed to download image")}
