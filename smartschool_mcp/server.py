"""
Smartschool MCP Server
Provides tools to interact with Smartschool API for courses, results, tasks,
messages and more.
"""

from __future__ import annotations

import os
import re
import threading
from datetime import date, timedelta
from functools import lru_cache
from typing import Any, TypedDict
from urllib.parse import urlparse

from cachetools import TTLCache, cached
from mcp.server.fastmcp import FastMCP
from smartschool import (
    AppCredentials,
    Attachments,
    BoxType,
    Courses,
    EnvCredentials,
    FutureTasks,
    Message,
    MessageHeaders,
    Periods,
    PlannedElements,
    Reports,
    Results,
    Smartschool,
    StudentSupportLinks,
)

# MCP server - tools are registered via @mcp.tool() decorators below
mcp = FastMCP("Smartschool MCP")


class AuthenticationError(RuntimeError):
    """Authentication state is present but credentials are no longer valid."""


@lru_cache(maxsize=1)
def _env_session() -> Smartschool:
    """Cached session using environment-variable credentials (single-user mode).

    Lazy-initialized on first tool invocation so that import-time errors
    (missing env vars, network failures) surface as tool errors rather than
    crashing the process on startup.
    """
    return Smartschool(EnvCredentials())


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

    return Smartschool(
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


def _organiser_names(element: object) -> list[str]:
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


def _planned_element_dict(element: object) -> dict[str, Any]:
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
        "courses": [c.name for c in courses],
        "locations": [getattr(loc, "title", str(loc)) for loc in locations],
        "organisers": _organiser_names(element),
        "unconfirmed": getattr(element, "unconfirmed", None),
        "pinned": getattr(element, "pinned", None),
        "assignment_type": (
            assignment_type.name if assignment_type is not None else None
        ),
    }


_ACCOUNT_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
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


def _safe_account_id(account_id: str | int) -> str | None:
    stripped = str(account_id).strip()
    if not stripped or not _ACCOUNT_ID_RE.fullmatch(stripped):
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


def _person_name(data: dict[str, Any]) -> str | None:
    first = _pick(data, "firstName", "first_name", "voornaam")
    last = _pick(data, "lastName", "last_name", "surname", "naam")
    if isinstance(first, str) and isinstance(last, str):
        joined = f"{first} {last}".strip()
        if joined:
            return joined
    name = _pick(data, "name", "fullName", "full_name")
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
    if isinstance(name, str) and name.strip():
        return name.strip()
    if isinstance(first, str) and first.strip():
        return first.strip()
    if isinstance(last, str) and last.strip():
        return last.strip()
    return None


def _person_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Map a Studentcard / authenticatedUser object to stable MCP fields."""
    account_id = _pick(data, "accountID", "accountId", "account_id")
    user_id = _pick(data, "id", "userID", "userId", "user_id")
    return {
        "account_id": None if account_id is None else str(account_id),
        "user_id": None if user_id is None else str(user_id),
        "username": _pick(data, "username", "userName", "user_name"),
        "name": _person_name(data),
        "first_name": _pick(data, "firstName", "first_name", "voornaam"),
        "last_name": _pick(data, "lastName", "last_name", "surname"),
        "class_name": _pick(data, "className", "class_name", "class", "klas"),
        "platform": _pick(
            data,
            "platform",
            "platformUrl",
            "platform_url",
            "mainUrl",
            "main_url",
        ),
        "is_current": _pick(data, "isCurrent", "is_current", "current"),
    }


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


def _follow_switched_host(session: Smartschool, response: Any) -> str | None:
    """If Mijn kinderen jumped to another school host, retarget the session."""
    final_url = getattr(response, "url", "") or ""
    new_host = urlparse(final_url).netloc
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
    return new_host


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
        return [{"error": f"Failed to retrieve courses: {e!s}"}]


@mcp.tool()
def get_results(
    limit: int = 15,
    offset: int = 0,
    course_filter: str | None = None,
    include_details: bool = True,
) -> dict[str, Any]:
    """
    Retrieve student results/grades with detailed information.

    Args:
        limit: Maximum number of results to return (default: 15)
        offset: Number of results to skip from the beginning (default: 0)
        course_filter: Filter results by course name (partial match, case-insensitive)
        include_details: Whether to fetch detailed info (teacher, average, median)
            - saves API calls if False

    Returns:
        Dictionary with results list and pagination info.

    Examples:
        - get_results() -> First 15 results with details
        - get_results(course_filter="Math") -> Results from courses containing "Math"
        - get_results(include_details=False) -> Basic info only, faster response
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

        results_list = []

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

            # Fetch central tendencies (average/median) only when requested
            if include_details:
                result_data.update({"average": None, "median": None})

                try:
                    # details is a lazy-loaded property — fetches on first access
                    detail = result.details

                    # Extract statistical information (central tendencies)
                    if detail and detail.central_tendencies:
                        tendencies = detail.central_tendencies

                        if len(tendencies) > 0 and hasattr(tendencies[0], "graphic"):
                            g = tendencies[0].graphic
                            result_data["average"] = {
                                "description": getattr(g, "description", "N/A"),
                                "value": getattr(g, "value", None),
                            }

                        if len(tendencies) > 1 and hasattr(tendencies[1], "graphic"):
                            g = tendencies[1].graphic
                            result_data["median"] = {
                                "description": getattr(g, "description", "N/A"),
                                "value": getattr(g, "value", None),
                            }

                except Exception:
                    # central_tendencies unavailable — keep default None values
                    pass

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
        return {"error": f"Failed to retrieve results: {e!s}"}


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


@mcp.tool()
def get_future_tasks() -> dict[str, Any]:
    """
    Retrieve upcoming assignments and tasks.

    Returns:
        Dictionary with future tasks organized by date and course.
    """
    try:
        future_tasks = FutureTasks(_session())
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

                # Extract tasks from the course
                if hasattr(course, "items") and hasattr(course.items, "tasks"):
                    for task in course.items.tasks:
                        task_data: _TaskDict = {
                            "label": getattr(task, "label", "N/A"),
                            "description": getattr(task, "description", "N/A"),
                            "warning": getattr(task, "warning", False),
                        }
                        course_data["tasks"].append(task_data)

                # Only add course if it has tasks
                if course_data["tasks"]:
                    day_data["courses"].append(course_data)

            # Only add day if it has courses with tasks
            if day_data["courses"]:
                tasks_data.append(day_data)

        total_tasks = sum(
            len(course["tasks"]) for day in tasks_data for course in day["courses"]
        )

        return {
            "future_tasks": tasks_data,
            "total_days": len(tasks_data),
            "total_tasks": total_tasks,
        }

    except Exception as e:
        return {"error": f"Failed to retrieve future tasks: {e!s}"}


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
        return {"error": f"Failed to retrieve messages: {e!s}"}


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
        Dictionary with planned elements for the given date.
    """
    try:
        target_date = date.today() + timedelta(days=date_offset)
        elements_list = []

        for element in PlannedElements(
            _session(),
            from_date=target_date,
            till_date=target_date,
            includes=_csv_or_none(includes),
        ):
            elements_list.append(_planned_element_dict(element))

        return {
            "date": target_date.strftime("%Y-%m-%d"),
            "elements": elements_list,
            "total": len(elements_list),
        }

    except Exception as e:
        return {"error": f"Failed to retrieve schedule: {e!s}"}


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
        return [{"error": f"Failed to retrieve periods: {e!s}"}]


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
        return [{"error": f"Failed to retrieve reports: {e!s}"}]


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
        Dictionary with planned elements including dates, courses, and assignment types.
    """
    try:
        start = date.fromisoformat(from_date) if from_date else date.today()
        end = (
            date.fromisoformat(to_date)
            if to_date
            else start + timedelta(days=days_ahead)
        )
        elements_list = []

        for element in PlannedElements(
            _session(),
            from_date=start,
            till_date=end,
            types=_csv_or_none(types),
            includes=_csv_or_none(includes),
        ):
            elements_list.append(_planned_element_dict(element))

        return {
            "planned_elements": elements_list,
            "total": len(elements_list),
            "period": {
                "from": start.strftime("%Y-%m-%d"),
                "to": end.strftime("%Y-%m-%d"),
            },
        }

    except Exception as e:
        return {"error": f"Failed to retrieve planned elements: {e!s}"}


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
        return [{"error": f"Failed to retrieve support links: {e!s}"}]


@mcp.tool()
def get_children() -> dict[str, Any]:
    """
    List children linked on Mijn kinderen for this parent/co-account.

    Same portal call as the website: POST /Studentcard/Student/getStudents.
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
        payload = session.json("/Studentcard/Student/getStudents", method="post")
        current = _current_user_dict(session)
        children = [
            _mark_current_child(_person_dict(raw), current)
            for raw in _as_child_records(payload)
        ]
        return {
            "children": children,
            "current": current,
            "total": len(children),
        }
    except Exception as e:
        return {"error": f"Failed to retrieve children: {e!s}"}


@mcp.tool()
def switch_child(account_id: str) -> dict[str, Any]:
    """
    Switch the session to another linked child (Mijn kinderen).

    Same portal call as the website: GET
    /Studentcard/Chain/gotourl/accountID/{accountId}. After a successful
    switch, get_schedule / get_planned_elements / get_results follow the
    newly selected child. Get account_id from get_children.

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
        response = session.get(f"/Studentcard/Chain/gotourl/accountID/{safe_id}")
        if not getattr(response, "ok", True):
            status = getattr(response, "status_code", "?")
            return {"error": f"Child switch failed: HTTP {status}"}

        switched_host = _follow_switched_host(session, response)
        user = _authenticated_user_from_response(session, response)
        if user is None:
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
        return {"error": f"Failed to switch child: {e!s}"}


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
        return {"error": f"Failed to retrieve attachments: {e!s}"}


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
        return {"error": f"Failed to download attachment: {e!s}"}


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
        return {"error": f"Failed to retrieve homepage blocks: {e!s}"}


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
        return {"error": f"Failed to download image: {e!s}"}
