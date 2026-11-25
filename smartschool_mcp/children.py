"""Mijn kinderen: list the children linked to a parent login and switch between them.

The website's own calls: ``POST /Studentcard/Student/getStudents`` lists the
children; ``GET /Studentcard/Chain/gotourl/accountID/{accountId}`` switches.
A child on another school platform hops through a one-time ``/otp/...`` link
to that school, which may ask for the birth date of the child being switched
to. That answer goes out as a raw POST, never with this login's password or
TOTP, and a wrong answer writes no ``auth_failed`` marker.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from smartschool import Smartschool
from smartschool._common import fill_form

from smartschool_mcp.guard import auth_failed_path

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
    html = BeautifulSoup(getattr(response, "text", "") or "", "html.parser")
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


def _path_is(url: str, segment: str) -> bool:
    return segment in set(urlparse(url).path.split("/"))


def _post_child_verification(
    session: Smartschool, url: str, origin_host: str, birth_date: str
) -> Any:
    """Answer another school's "birth date of the child" question, once.

    The other school asks for the birth date of the child being switched to.
    The answer is posted with a raw request: no password, no TOTP, and no
    ``GuardedSession`` bookkeeping, so a wrong date can neither re-post the
    login nor write an ``auth_failed`` lock for a password that was never sent.
    """
    page = _foreign_hop_get(session, url, origin_host)
    data = fill_form(
        page,
        'form[name="account_verification_form"]',
        {"security_question_answer": birth_date},
    )
    headers = dict(_BROWSER_NAV_HEADERS)
    headers["Referer"] = url
    response = _raw_session_request(
        session, "POST", url, data=data, allow_redirects=True, headers=headers
    )
    if response is None:
        raise RuntimeError("no raw HTTP session available for account verification")
    return response


def _clear_stale_lock(session: Smartschool) -> None:
    """Drop an ``auth_failed`` lock the current host got from a failed switch.

    Older versions posted the wrong child's birth date to the other school and
    locked that school's profile for it. No password was ever sent there, and
    the verification just succeeded, so the lock is stale and would otherwise
    refuse every call on this host.
    """
    try:
        auth_failed_path(session).unlink(missing_ok=True)
    except (OSError, RuntimeError):
        pass


def _child_name_for(session: Smartschool, account_id: str) -> str | None:
    """Name of a linked child, as ``get_children`` lists it."""
    try:
        for child in list_children(session).get("children") or []:
            if child.get("account_id") == account_id:
                name = child.get("first_name") or child.get("name")
                return str(name) if name else None
    except Exception:
        return None
    return None


def _children_file(session: Smartschool) -> Path | None:
    """``children.json`` in the cache of the school this login belongs to.

    It holds the birth dates other schools accepted when switching to a child
    there, keyed by account id, so the user is asked for each date once.
    """
    creds = getattr(session, "creds", None)
    username = str(getattr(creds, "username", "") or "")
    home = session.__dict__.get("_home_host") or str(
        getattr(creds, "main_url", "") or ""
    )
    if not username or not home or "/" in username or username.startswith("."):
        return None
    return Path.home() / ".cache" / "smartschool" / home / username / "children.json"


def _load_children_file(session: Smartschool) -> dict[str, Any]:
    path = _children_file(session)
    if path is None or not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _stored_child_birth_date(session: Smartschool, account_id: str) -> str | None:
    """Birth date another school accepted for this child before."""
    entry = _load_children_file(session).get(account_id)
    birth = entry.get("birth_date") if isinstance(entry, dict) else None
    return birth if isinstance(birth, str) else None


def _save_child_birth_date(
    session: Smartschool, account_id: str, name: str | None, birth_date: str
) -> bool:
    """Remember a birth date the other school accepted. Private file (0600)."""
    path = _children_file(session)
    if path is None:
        return False
    data = _load_children_file(session)
    data[account_id] = {"name": name, "birth_date": birth_date}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    with contextlib.suppress(OSError):
        os.chmod(path, 0o600)
    return True


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
    session.__dict__.setdefault("_home_host", current_host)
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
    session: Smartschool, start_path: str, birth_date: str | None = None
) -> tuple[Any, str | None, str | None]:
    """Follow gotourl hops without replaying the original host after OTP/auth.

    Cross-school children 302 to ``https://other.smartschool.be/otp/...``. The
    smartschool session would then finish De Ring login and replay the original
    De Pass gotourl, dropping the new cookies. Hop-by-hop avoids that replay.

    Never GET another school's ``/login`` through ``session.get``:
    ``Smartschool.request`` POSTs this session's username/password on any
    login form. Foreign hops (``/otp/...``, then relative ``/Studentcard``)
    use a raw GET with browser navigation headers. A foreign
    ``account-verification`` asks for the birth date of the child being
    switched to: it is answered once with ``birth_date`` through a raw POST,
    never with this login's password or TOTP. Without a ``birth_date`` nothing
    is posted. Any other foreign auth page stops the switch.
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
            answer = birth_date
            if not answer and urlparse(url).netloc == session.__dict__.get(
                "_home_host"
            ):
                # Back on the school this login belongs to: its own ``mfa``.
                answer = getattr(getattr(session, "creds", None), "mfa", None)
            if not _path_is(url, "account-verification") or not answer:
                return response, switched_host, url
            try:
                response = _post_child_verification(session, url, origin_host, answer)
            except Exception:
                return response, switched_host, url
            landing = str(getattr(response, "url", "") or url)
            if _url_is_auth(landing):
                return response, switched_host, landing
            _clear_stale_lock(session)
            session.__dict__["_verified_birth"] = answer
            return response, switched_host, None
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


def list_children(session: Smartschool) -> dict[str, Any]:
    """The children on Mijn kinderen, with ``current`` and ``total``."""
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
    return {"children": children, "current": current, "total": len(children)}


_ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def child_birth_date(raw: str) -> str | None:
    """A birth date as ``YYYY-MM-DD`` (``dd-mm-yyyy`` accepted), or None."""
    from smartschool_mcp.settings import SettingsError, verification_answer

    try:
        answer = verification_answer(raw)
    except SettingsError:
        return None
    return answer if _ISO_DATE_RE.fullmatch(answer) else None


def switch(
    session: Smartschool, account_id: str, birth_date: str | None = None
) -> dict[str, Any]:
    """Switch *session* to the linked child *account_id*."""
    safe_id = _safe_account_id(account_id)
    if safe_id is None:
        return {"error": "Invalid account_id"}
    child_birth: str | None = None
    if birth_date:
        child_birth = child_birth_date(birth_date)
        if child_birth is None:
            return {"error": "birth_date must be dd-mm-yyyy, e.g. 21-03-2014"}

    session.ensure_authenticated()
    origin_host = urlparse(session.create_url("/")).netloc
    path = f"/Studentcard/Chain/gotourl/accountID/{safe_id}"
    stored_birth = _stored_child_birth_date(session, safe_id)
    # Remember a date the other school accepted, once. The child's name is
    # read now, while the session is still on the school it belongs to.
    new_birth = child_birth if child_birth and child_birth != stored_birth else None
    child_name = _child_name_for(session, safe_id) if new_birth else None
    child_birth = child_birth or stored_birth
    session.__dict__.pop("_verified_birth", None)
    response, switched_host, blocked_login = _follow_child_switch_redirects(
        session, path, child_birth
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
        error = (
            f"Child switch landed on the other school's {auth_kind}; "
            "not submitting this account's password there"
        )
        if _path_is(blocked_login or landing, "account-verification"):
            if child_birth:
                error = (
                    "The other school did not accept the birth date for this "
                    "child. Nothing else was sent; check the date and try again."
                )
            else:
                error = (
                    "The other school asks for the birth date of this child. "
                    "Ask the user for it and call switch_child again with "
                    "birth_date (dd-mm-yyyy); it is stored once accepted. "
                    "Nothing was sent."
                )
        failed: dict[str, Any] = {"ok": False, "error": error, "account_id": safe_id}
        if switched_host:
            failed["switched_host"] = switched_host
        return failed

    user = _authenticated_user_from_response(session, response)
    if user is None and not _url_is_auth(landing):
        user = _authenticated_user_from_response(session, session.get("/"))

    current = (
        _person_dict(user) if isinstance(user, dict) else _current_user_dict(session)
    )
    result: dict[str, Any] = {"ok": True, "account_id": safe_id, "current": current}
    if switched_host:
        result["switched_host"] = switched_host
    if new_birth and session.__dict__.get("_verified_birth") == new_birth:
        try:
            result["saved_birth_date"] = _save_child_birth_date(
                session, safe_id, child_name, new_birth
            )
        except OSError:
            result["saved_birth_date"] = False
    return result
