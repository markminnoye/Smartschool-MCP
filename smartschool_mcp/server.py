"""
Smartschool MCP Server
Provides tools to interact with Smartschool API for courses, results, tasks,
messages and more.
"""

from __future__ import annotations

import contextlib
import os
import re
import threading
from datetime import date, timedelta
from pathlib import Path
from typing import Any, TypedDict
from urllib.parse import urlparse

from cachetools import TTLCache, cached
from mcp.server.mcpserver import MCPServer
from smartschool import (
    AppCredentials,
    Attachments,
    BoxType,
    Courses,
    FolderItem,
    FutureTasks,
    InternetShortcut,
    Message,
    MessageHeaders,
    Periods,
    PlannedElement,
    PlannedElements,
    Reports,
    Results,
    Smartschool,
    SmartschoolLessons,
    SmartSchoolRequestError,
    StudentAbsences,
    StudentNoteDetails,
    StudentNotes,
    StudentSupportLinks,
    TopNavCourses,
)

from smartschool_mcp import children, content
from smartschool_mcp.guard import GuardedSession, MemoryGuardedSession
from smartschool_mcp.settings import env_credentials
from smartschool_mcp import content
from smartschool_mcp.result_feedback import relax_feedback_attachments

FastMCP = MCPServer  # Backward compatibility alias

# MCP server - tools are registered via @mcp.tool() decorators below
mcp = MCPServer("Smartschool MCP")

# Some schools send feedback attachments as objects; see result_feedback.py.
relax_feedback_attachments()


class AuthenticationError(RuntimeError):
    """Authentication state is present but credentials are no longer valid."""


# How long (seconds) a cached Smartschool session is reused before the next
# request for those credentials creates a fresh one.  Cookie-based sessions
# expire server-side; keeping this below the server's idle-session timeout
# prevents stale-cookie failures.  Override with SESSION_TTL_SECONDS env var.
_SESSION_TTL_SECONDS = int(os.environ.get("SESSION_TTL_SECONDS", "3600"))
_session_cache: TTLCache[tuple[str, str, str, str], Smartschool] = TTLCache(
    maxsize=256, ttl=_SESSION_TTL_SECONDS
)
_session_cache_lock = threading.Lock()

_env_session_cache: TTLCache[tuple[()], Smartschool] = TTLCache(
    maxsize=1, ttl=_SESSION_TTL_SECONDS
)
_env_session_lock = threading.Lock()


@cached(cache=_env_session_cache, lock=_env_session_lock)
def _env_session() -> Smartschool:
    """TTL-cached session using environment-variable credentials.

    Lazy-initialized on first tool invocation so that import-time errors
    (missing env vars, network failures) surface as tool errors rather than
    crashing the process on startup.

    The TTL matters most in stdio mode, where a single long-lived process
    would otherwise reuse one session object for days.  See
    ``_ensure_live_session`` for why an expired cookie is not self-healing.
    """
    # Checked and tidied first: a typo must not cost a login attempt.
    return GuardedSession(env_credentials())


# Path segments Smartschool redirects to when a session is not authenticated.
# Mirrors ``smartschool.Smartschool._is_auth_url`` without depending on a
# private API.
_AUTH_PATH_SEGMENTS = frozenset({"login", "account-verification", "2fa"})

# Attribute used to remember the liveness-probe outcome on a session object,
# so the probe runs once per cached session rather than once per tool call.
_PROBE_RESULT_ATTR = "_mcp_liveness_probe"


def _is_auth_url(url: str) -> bool:
    """Return True if *url* is one of Smartschool's authentication pages."""
    return bool(_AUTH_PATH_SEGMENTS & set(urlparse(url).path.split("/")))


def _ensure_live_session(session: Smartschool) -> Smartschool:
    """Verify *session* is really authenticated, re-logging in if it is not.

    ``Smartschool.ensure_authenticated()`` is not a liveness check: it only
    asks whether an ``authenticated_user`` record exists, and
    ``Smartschool.__post_init__`` restores that record from
    ``<cache>/authenticated_user.yml`` on every new instance.  A session whose
    persisted cookie has expired server-side therefore still reports itself as
    authenticated.

    That stays invisible for REST endpoints, which redirect to ``/login`` so
    that ``Session.request`` transparently drives the login chain and retries.
    But the XML dispatcher backing messages, attachments and future tasks
    answers an unauthenticated POST with an **empty 200**, which the library
    reads as an empty result set -- so those tools report an empty mailbox
    instead of an error.

    One plain ``GET /`` closes the gap: it goes through ``Session.request``, so
    a dead cookie triggers the real login chain while a live one costs a single
    request and no login.  The outcome is remembered on the session object, so
    a failure is re-raised for the rest of that session's TTL instead of
    re-attempting a login on every tool call.
    """
    probe = getattr(session, _PROBE_RESULT_ATTR, None)
    if probe is None:
        try:
            response = session.get("/")
            probe = (
                AuthenticationError(
                    "Smartschool session is not authenticated; check the "
                    f"SMARTSCHOOL_* credentials (landed on {response.url!r})"
                )
                if _is_auth_url(response.url)
                else False
            )
        except Exception as exc:
            # Remembered and re-raised below, so one bad login is not retried
            # on every subsequent tool call.
            probe = exc
        with contextlib.suppress(AttributeError):
            setattr(session, _PROBE_RESULT_ATTR, probe)

    if isinstance(probe, BaseException):
        raise probe
    return session


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

    return MemoryGuardedSession(
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
            return _ensure_live_session(
                _cached_app_session(
                    creds.username, creds.password, creds.main_url, creds.mfa
                )
            )
    except ImportError:
        pass

    return _ensure_live_session(_env_session())


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


def _feedback_attachment_ids(result: Any) -> list[str]:
    """Ids of the files teachers attached to a result's feedback."""
    ids: list[str] = []
    for feedback in getattr(result, "feedbacks", None) or []:
        ids.extend(str(i) for i in getattr(feedback, "attachments", None) or [])
    return ids


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
                "feedback_attachment_ids": _feedback_attachment_ids(result),
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


# Planner element types that make up a day's timetable: lessons with content,
# empty timetable slots, and moments of a lesson cluster.
_PLANNER_LESSON_TYPES = (
    "planned-lessons",
    "planned-placeholders",
    "planned-lesson-cluster-moments",
)
_PLANNER_TASK_TYPES = ("planned-assignments", "planned-to-dos")


def _planner_location(element: Any) -> str | None:
    names = [
        getattr(loc, "title", None)
        or getattr(loc, "text", None)
        or getattr(loc, "number", None)
        for loc in getattr(element, "locations", None) or []
    ]
    return ", ".join(n for n in names if n) or None


def _planner_organisers(element: Any) -> str | None:
    users = getattr(getattr(element, "organisers", None), "users", None) or []
    names = []
    for user in users:
        with contextlib.suppress(AttributeError):
            names.append(user.name.starting_with_last_name)
    return ", ".join(names) or None


def _planner_future_tasks(session: Smartschool, days_ahead: int = 34) -> list[_DayDict]:
    """Planner assignments and to-dos, shaped like the agenda's future tasks."""
    days: dict[str, dict[str, list[_TaskDict]]] = {}
    elements = PlannedElements(
        session,
        till_date=date.today() + timedelta(days=days_ahead),
        element_types=_PLANNER_TASK_TYPES,
    )
    for element in sorted(elements, key=lambda e: e.period.date_time_from):
        day = element.period.date_time_from.strftime("%Y-%m-%d")
        course = ", ".join(c.name for c in element.courses) or "-"
        label = (
            element.assignment_type.name
            if element.assignment_type
            else (
                "To-do" if element.planned_element_type == "planned-to-dos" else "N/A"
            )
        )
        days.setdefault(day, {}).setdefault(course, []).append(
            {"label": label, "description": element.name, "warning": False}
        )
    return [
        {
            "date": day,
            "courses": [
                {"name": name, "tasks": tasks} for name, tasks in courses.items()
            ],
        }
        for day, courses in days.items()
    ]


def _planner_lessons(session: Smartschool, target_date: date) -> list[dict[str, Any]]:
    """A day's timetable from the planner, in the shape of the agenda lessons."""
    elements = PlannedElements(
        session,
        from_date=target_date,
        till_date=target_date + timedelta(days=1),
        element_types=_PLANNER_LESSON_TYPES,
    )
    lessons = []
    for element in elements:
        period = element.period
        if period.date_time_from.date() != target_date:
            continue
        lessons.append(
            {
                "moment_id": str(element.id),
                "date": target_date.strftime("%Y-%m-%d"),
                "hour": f"{period.date_time_from:%H:%M}-{period.date_time_to:%H:%M}",
                "course": ", ".join(c.name for c in element.courses) or None,
                "classroom": _planner_location(element),
                "teacher": _planner_organisers(element),
                "subject": element.name or None,
                "note": None,
                "color": element.color,
                "assignment_end_status": None,
                "test_deadline_status": None,
                "type": element.planned_element_type,
            }
        )
    return sorted(lessons, key=lambda lesson: lesson["hour"])


@mcp.tool()
def get_future_tasks() -> dict[str, Any]:
    """
    Retrieve upcoming assignments and tasks.

    Reads the agenda's future tasks; when the agenda is empty, as at schools
    that use the planner, reads the planner's assignments and to-dos for the
    next 34 days instead.

    Returns:
        Dictionary with future tasks organized by date and course, and
        ``source`` ("agenda" or "planner").
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

        source = "agenda"
        planner_error = None
        if not tasks_data:
            # Schools that moved to the planner leave the old agenda empty.
            try:
                tasks_data = _planner_future_tasks(_session())
                source = "planner"
            except Exception as e:
                planner_error = f"{e!s}"

        total_tasks = sum(
            len(course["tasks"]) for day in tasks_data for course in day["courses"]
        )

        return {
            "future_tasks": tasks_data,
            "total_days": len(tasks_data),
            "total_tasks": total_tasks,
            "source": source,
            **({"planner_error": planner_error} if planner_error else {}),
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
        all_headers = list(MessageHeaders(_session(), box_type=box_type_enum))

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
                    full_msg = Message(_session(), header.id).get()
                    body = (getattr(full_msg, "body", "") or "").lower()
                    if search_query.lower() in body:
                        header._cached_message = full_msg  # type: ignore[attr-defined]
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
                    full_msg = cached or Message(_session(), header.id).get()
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
def get_schedule(date_offset: int = 0) -> dict[str, Any]:
    """
    Retrieve the lesson schedule for a given day.

    Args:
        date_offset: Days from today (0=today, 1=tomorrow, -1=yesterday,
            default: 0)

    Reads the agenda; when it has no lessons that day, as at schools that use
    the planner, reads the planner's lessons, timetable slots and cluster
    moments for that day instead.

    Returns:
        Dictionary with the lessons scheduled for the given date, and
        ``source`` ("agenda" or "planner").
    """
    try:
        target_date = date.today() + timedelta(days=date_offset)
        lessons_list = []

        for lesson in SmartschoolLessons(_session(), timestamp_to_use=target_date):
            lessons_list.append(
                {
                    "moment_id": lesson.moment_id,
                    "date": _safe_format_date(lesson.date),
                    "hour": lesson.hour,
                    "course": lesson.course_title,
                    "classroom": lesson.classroom_title,
                    "teacher": lesson.teacher_title,
                    "subject": lesson.subject,
                    "note": lesson.note,
                    "color": lesson.color,
                    "assignment_end_status": lesson.assignment_end_status,
                    "test_deadline_status": lesson.test_deadline_status,
                }
            )

        source = "agenda"
        planner_error = None
        if not lessons_list:
            # Schools that moved to the planner leave the old agenda empty.
            try:
                lessons_list = _planner_lessons(_session(), target_date)
                source = "planner"
            except Exception as e:
                planner_error = f"{e!s}"

        return {
            "date": target_date.strftime("%Y-%m-%d"),
            "lessons": lessons_list,
            "total": len(lessons_list),
            "source": source,
            **({"planner_error": planner_error} if planner_error else {}),
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


# Elements fetched with get_planned_elements, by id, so the detail tools can
# find an element's type and platform without listing the planner again.
_planner_index: dict[str, tuple[str, int]] = {}
# Asked along with the planner list, so each element carries what its detail
# page holds (description, attachments, links, to-dos) without a request each.
# Asking for any `includes` drops the default ones, hence icon/courses/locations.
_PLANNER_LIST_INCLUDES = ",".join(
    (
        "icon",
        "courses",
        "locations",
        "attachments",
        "weblinks",
        "deeplinks",
        "partner-weblinks",
        "public-info",
        "planned-to-dos",
    )
)
# Upper bound on the folders a tool walks, so a large course stays quick.
_MAX_FOLDERS = 40
_DETAIL_KEYS = ("description", "attachments", "links", "links_without_url", "to_dos")


def _planner_element_summary(element: Any) -> dict[str, Any]:
    period = element.period
    assignment_type = element.assignment_type.name if element.assignment_type else None
    return {
        "id": str(element.id),
        "platform_id": element.platform_id,
        "name": element.name,
        "type": element.planned_element_type,
        "type_label": content.type_label(element.planned_element_type),
        "is_exam": content.is_exam(element.name, assignment_type),
        "from": period.date_time_from.strftime("%Y-%m-%d %H:%M"),
        "to": period.date_time_to.strftime("%Y-%m-%d %H:%M"),
        "color": element.color,
        "courses": [c.name for c in element.courses],
        "unconfirmed": element.unconfirmed,
        "pinned": element.pinned,
        "assignment_type": assignment_type,
    }


def _planner_list(
    session: Smartschool,
    from_date: date,
    till_date: date,
    types: list[str] | None,
) -> list[dict[str, Any]]:
    """
    The planner elements of the logged-in user, with their content.

    The same request as the library's ``PlannedElements``, plus ``includes``:
    the library has no way to ask for them.
    """
    params = {
        "from": from_date.isoformat(),
        "to": till_date.isoformat(),
        "includes": _PLANNER_LIST_INCLUDES,
    }
    if types:
        params["types"] = ",".join(types)
    user_id = session.authenticated_user["id"]
    elements = session.json(
        f"/planner/api/v1/planned-elements/user/{user_id}", data=params
    )
    return elements if isinstance(elements, list) else []


def _same_test(lesson: dict[str, Any], other: dict[str, Any]) -> bool:
    """
    Whether *lesson* repeats the assignment *other*.

    A test is often planned twice, as a lesson and as an assignment, on the
    same day and for the same course, the assignment's name sometimes longer
    ("... (see info)"). The assignment is the one to keep.
    """
    if lesson["type"] != "planned-lessons" or other["type"] == "planned-lessons":
        return False
    if lesson["from"][:10] != other["from"][:10]:
        return False
    if not set(lesson["courses"]) & set(other["courses"]):
        return False
    a, b = lesson["name"].strip().lower(), other["name"].strip().lower()
    return bool(a and b) and (a.startswith(b) or b.startswith(a))


def _planner_detail_url(element_type: str, platform_id: int, element_id: str) -> str:
    return f"/planner/api/v1/{element_type}/{platform_id}/{element_id}"


def _locate_planner_element(
    session: Smartschool, element_id: str, element_type: str | None
) -> tuple[str, int]:
    """The type and platform of a planner element, listing the planner if needed."""
    known = _planner_index.get(element_id)
    if known and element_type in (None, known[0]):
        return known
    today = date.today()
    for element in PlannedElements(
        session,
        from_date=today - timedelta(days=30),
        till_date=today + timedelta(days=120),
        element_types=element_type,
    ):
        _planner_index[str(element.id)] = (
            element.planned_element_type,
            element.platform_id,
        )
    if element_id not in _planner_index:
        raise LookupError(
            f"Planner element {element_id} not found between 30 days ago "
            "and 120 days ahead"
        )
    return _planner_index[element_id]


@mcp.tool()
def get_planned_elements(
    days_ahead: int = 34,
    types: list[str] | None = None,
    include_details: bool = False,
) -> dict[str, Any]:
    """
    Retrieve what the Smartschool planner holds for the coming days.

    By default: assignments, to-dos, events (exam periods, report days, parent
    evenings), days off and school activities, plus the lessons that are an
    exam or a test (``is_exam``). The rest of the timetable is left out; ask
    for it with ``types``.

    Args:
        days_ahead: Number of days ahead to fetch (default: 34)
        types: Planner types to return, e.g. ["planned-lessons"]. Known types:
            planned-assignments, planned-to-dos, planned-lessons,
            planned-meetings, planned-lesson-free-days,
            planned-school-activities, planned-activities, planned-generics,
            planned-placeholders, planned-lesson-cluster-moments.
        include_details: Also return each element's description, attachments
            and links (see get_planned_element_details). They come with the
            same request; the option only makes the result longer.

    Returns:
        Dictionary with the planned elements: id, name, type, readable
        ``type_label``, ``is_exam``, dates, courses, assignment type, and
        whether the element has more to show: ``has_attachments``,
        ``has_links`` (links with an address, or Bookwidget titles without
        one) and ``has_description`` (a description that says more than the
        name). ``details`` holds that content when asked for.
    """
    try:
        session = _session()
        from_date = date.today()
        till_date = from_date + timedelta(days=days_ahead)
        base_url = session.create_url("/")
        raw_elements = [
            (PlannedElement(**raw), raw)
            for raw in _planner_list(session, from_date, till_date, types)
        ]
        elements_list = []
        for element, raw in sorted(
            raw_elements, key=lambda pair: pair[0].period.date_time_from
        ):
            _planner_index[str(element.id)] = (
                element.planned_element_type,
                element.platform_id,
            )
            summary = _planner_element_summary(element)
            detail = content.planner_detail(raw, base_url)
            summary.update(content.content_flags(detail))
            if include_details:
                summary["details"] = {key: detail[key] for key in _DETAIL_KEYS}
            if not types and (
                summary["type"] in content.EMPTY_SLOT_TYPES
                or (summary["type"] == "planned-lessons" and not summary["is_exam"])
            ):
                continue
            elements_list.append(summary)
        if not types:
            elements_list = [
                s
                for s in elements_list
                if not any(_same_test(s, other) for other in elements_list)
            ]

        return {
            "planned_elements": elements_list,
            "total": len(elements_list),
            "period": {
                "from": from_date.strftime("%Y-%m-%d"),
                "to": till_date.strftime("%Y-%m-%d"),
            },
        }

    except Exception as e:
        return {"error": f"Failed to retrieve planned elements: {e!s}"}


@mcp.tool()
def get_planned_element_details(
    element_id: str, element_type: str | None = None
) -> dict[str, Any]:
    """
    Read one planner element in full: description, attachments and links.

    Args:
        element_id: The element's ``id`` from get_planned_elements.
        element_type: Its ``type``; looked up when left out.

    Returns:
        The element with its description as text, ``attachments`` (id, name,
        size; download them with download_planned_attachment), ``links``
        (label, url, kind: weblink, partner, course_link, document or
        description; a document link carries ``course_id`` and ``doc_id``:
        a file for download_course_document, a folder to open as
        ``folder_id`` with get_course_documents) and ``links_without_url``:
        titles linked in
        the description without an address, usually Bookwidgets to look up
        with get_learning_path.
    """
    try:
        session = _session()
        element_type, platform_id = _locate_planner_element(
            session, element_id, element_type
        )
        url = _planner_detail_url(element_type, platform_id, element_id)
        return content.planner_detail(session.json(url), session.create_url("/"))
    except Exception as e:
        return {"error": f"Failed to retrieve planner element details: {e!s}"}


def _save_download(data: bytes, name: str, save_path: str | None) -> Path:
    """
    Write a download under *save_path* or ~/Downloads/smartschool/.

    The name is reduced to its last path component (no path traversal) and an
    existing file is never overwritten: a counter suffix is added instead
    (``report (1).pdf``).
    """
    download_dir = (
        Path(save_path) if save_path else Path.home() / "Downloads" / "smartschool"
    )
    download_dir.mkdir(parents=True, exist_ok=True)
    file_path = download_dir / (Path(name).name or "download")
    counter = 1
    stem = file_path.stem
    while file_path.exists():
        file_path = download_dir / f"{stem} ({counter}){file_path.suffix}"
        counter += 1
    file_path.write_bytes(data)
    return file_path


@mcp.tool()
def download_planned_attachment(
    element_id: str,
    attachment_id: str,
    element_type: str | None = None,
    save_path: str | None = None,
) -> dict[str, Any]:
    """
    Download an attachment of a planner element.

    Files are saved to *save_path* when provided, otherwise to
    ~/Downloads/smartschool/. Existing files are never overwritten.

    Args:
        element_id: The element's ``id`` from get_planned_elements.
        attachment_id: The attachment's ``id`` from get_planned_element_details.
        element_type: The element's ``type``; looked up when left out.
        save_path: Optional directory to save the file into.

    Returns:
        Dictionary with the saved file path, name, mime type and bytes written.
    """
    try:
        session = _session()
        element_type, platform_id = _locate_planner_element(
            session, element_id, element_type
        )
        url = _planner_detail_url(element_type, platform_id, element_id)
        attachment = next(
            (
                a
                for a in session.json(url).get("attachments") or []
                if str(a.get("id")) == str(attachment_id)
            ),
            None,
        )
        if attachment is None:
            return {
                "error": f"Attachment {attachment_id} not found in element {element_id}"
            }
        resp = session.get(f"{url}/attachments/{attachment_id}/download")
        if not resp.ok:
            return {"error": f"Download failed: HTTP {resp.status_code}"}
        name = attachment.get("fileName") or f"attachment_{attachment_id}"
        file_path = _save_download(resp.content, name, save_path)
        return {
            "attachment_id": attachment_id,
            "name": file_path.name,
            "mime_type": attachment.get("mimeType"),
            "saved_to": str(file_path),
            "bytes_written": len(resp.content),
        }
    except Exception as e:
        return {"error": f"Failed to download planner attachment: {e!s}"}


# ── Course documents and leerpaden ────────────────────────────────────────────


def _find_course(session: Smartschool, course: str) -> Any:
    """A course from the top navigation, by id or by (part of) its name."""
    courses = list(TopNavCourses(session))
    wanted = str(course).strip()
    if wanted.isdigit():
        for candidate in courses:
            if str(candidate.id) == wanted:
                return candidate
    matches = [c for c in courses if wanted.lower() in c.name.lower()]
    exact = [c for c in matches if c.name.lower() == wanted.lower()]
    if len(exact) == 1 or len(matches) == 1:
        return (exact or matches)[0]
    names = ", ".join(f"{c.name} (id {c.id})" for c in matches or courses)
    raise LookupError(
        f"{'Several' if matches else 'No'} courses match {course!r}: {names}"
    )


def _course_list(session: Smartschool) -> list[dict[str, Any]]:
    return [
        {"id": c.id, "name": c.name, "teacher": c.teacher}
        for c in TopNavCourses(session)
    ]


def _document_folder(session: Smartschool, course: Any, folder_id: int | None) -> Any:
    browse_url = None
    if folder_id:
        browse_url = (
            f"/Documents/Index/Index/courseID/{course.id}"
            f"/parentID/{folder_id}/ssID/{course.platform_id}"
        )
    return FolderItem(
        session=session,
        parent=None,
        course=course,
        name="(Root)",
        browse_url=browse_url,
    )


def _document_entry(item: Any) -> dict[str, Any]:
    if isinstance(item, FolderItem):
        match = re.search(r"parentID/(\d+)", item.browse_url or "")
        return {
            "kind": "folder",
            "folder_id": int(match.group(1)) if match else None,
            "name": item.name,
        }
    if isinstance(item, InternetShortcut):
        return {"kind": "link", "name": item.name, "url": item.link}
    return {
        "kind": "file",
        "doc_id": item.id,
        "name": item.name,
        "mime_type": item.mime_type,
        "size_kb": item.size_kb,
        "modified": str(item.last_modified),
    }


@mcp.tool()
def get_course_documents(
    course: str | None = None, folder_id: int | None = None
) -> dict[str, Any]:
    """
    List the Documenten of a course, one folder at a time.

    Args:
        course: Course id or (part of) its name. Leave out to list the courses.
        folder_id: Folder to open (``folder_id`` of a folder entry); the
            course's top folder when left out.

    Returns:
        The folder's entries: folders (``folder_id``), files (``doc_id``,
        download with download_course_document) and web links (``url``).
    """
    try:
        session = _session()
        if not course:
            return {"courses": _course_list(session)}
        found = _find_course(session, course)
        entries = [
            _document_entry(item)
            for item in _document_folder(session, found, folder_id).items
        ]
        return {
            "course": {"id": found.id, "name": found.name},
            "folder_id": folder_id,
            "entries": entries,
            "total": len(entries),
        }
    except Exception as e:
        return {"error": f"Failed to retrieve course documents: {e!s}"}


@mcp.tool()
def download_course_document(
    course: str,
    doc_id: int,
    folder_id: int | None = None,
    save_path: str | None = None,
) -> dict[str, Any]:
    """
    Download a file from a course's Documenten.

    Files are saved to *save_path* when provided, otherwise to
    ~/Downloads/smartschool/. Existing files are never overwritten.

    Args:
        course: Course id or (part of) its name.
        doc_id: The file's ``doc_id`` from get_course_documents (or the
            ``document`` of a planner link).
        folder_id: The folder holding the file; when left out, the course's
            folders are searched.
        save_path: Optional directory to save the file into.

    Returns:
        Dictionary with the saved file path, name, mime type and bytes written.
    """
    try:
        session = _session()
        found = _find_course(session, course)
        pending = [_document_folder(session, found, folder_id)]
        visited = 0
        while pending and visited < _MAX_FOLDERS:
            folder = pending.pop(0)
            visited += 1
            for item in folder.items:
                if isinstance(item, FolderItem):
                    if folder_id is None:
                        pending.append(item)
                elif item.id == doc_id and not isinstance(item, InternetShortcut):
                    file_path = _save_download(
                        item.download(), item.filename, save_path
                    )
                    return {
                        "doc_id": doc_id,
                        "name": file_path.name,
                        "mime_type": item.mime_type,
                        "saved_to": str(file_path),
                        "bytes_written": file_path.stat().st_size,
                    }
        return {"error": f"Document {doc_id} not found in {found.name}"}
    except Exception as e:
        return {"error": f"Failed to download course document: {e!s}"}


def _lpaths_page(session: Smartschool, url: str) -> str | None:
    """A leerpaden page, or None when the course has no leerpaden module."""
    response = session.get(url)
    if "/Lpaths/" not in response.url:
        return None
    return response.text


@mcp.tool()
def get_learning_paths(course: str | None = None) -> dict[str, Any]:
    """
    List the leerpaden of a course, with the folders they are kept in.

    Args:
        course: Course id or (part of) its name. Leave out to list the courses.

    Returns:
        The course's leerpaden (``id``, ``name``, folder path); open one with
        get_learning_path.
    """
    try:
        session = _session()
        if not course:
            return {"courses": _course_list(session)}
        found = _find_course(session, course)
        result: dict[str, Any] = {"course": {"id": found.id, "name": found.name}}
        root = f"/Lpaths/Index/Index/courseID/{found.id}/ssID/{found.platform_id}"
        page = _lpaths_page(session, root)
        if page is None:
            return {**result, "learning_paths": [], "note": "No leerpaden module"}

        paths: list[dict[str, Any]] = []
        pending: list[tuple[str, list[str]]] = [(page, [])]
        seen: set[int] = set()
        while pending and len(seen) < _MAX_FOLDERS:
            page, trail = pending.pop(0)
            for entry in content.parse_lpath_folder(page):
                if entry["kind"] == "learning_path":
                    paths.append({**entry, "folder": " / ".join(trail) or None})
                elif entry["id"] and entry["id"] not in seen:
                    seen.add(entry["id"])
                    sub = _lpaths_page(
                        session,
                        f"/Lpaths/Index/Index/courseID/{found.id}"
                        f"/parentID/{entry['id']}",
                    )
                    if sub is not None:
                        pending.append((sub, [*trail, entry["name"]]))
        return {**result, "learning_paths": paths, "total": len(paths)}
    except Exception as e:
        return {"error": f"Failed to retrieve learning paths: {e!s}"}


def _course_exercises(session: Smartschool, course: Any) -> dict[int, dict[str, Any]]:
    """The course's external exercises (Bookwidgets, ...), by exercise id."""
    exercises: dict[int, dict[str, Any]] = {}
    pending = [0]
    seen: set[int] = set()
    while pending and len(seen) < _MAX_FOLDERS:
        parent = pending.pop(0)
        seen.add(parent)
        page = session.get(
            f"/?module=Exercises&file=index&courseID={course.id}"
            f"&parentID={parent}&ssID={course.platform_id}"
        ).text
        folders, found = content.parse_exercise_folder(page)
        exercises.update(found)
        pending.extend(f for f in folders if f not in seen and f not in pending)
    return exercises


def _weblink_url(session: Smartschool, course: Any, link_id: int) -> str | None:
    """Where a course weblink points: Smartschool answers with a redirect."""
    response = session.get(
        f"/Weblinks/Showlink/Index/courseID/{course.id}"
        f"/linkID/{link_id}/ssID/{course.platform_id}",
        allow_redirects=False,
    )
    return response.headers.get("Location") if response.is_redirect else None


@mcp.tool()
def get_learning_path(
    course: str, learning_path_id: int, resolve_links: bool = True
) -> dict[str, Any]:
    """
    Show the content of a leerpad: its sections and steps.

    Only the leerpad's overview is read; its steps are never opened, so the
    pupil's progress is left untouched.

    Args:
        course: Course id or (part of) its name.
        learning_path_id: The leerpad's ``id`` from get_learning_paths.
        resolve_links: Look up the address of web links and exercises
            (Bookwidgets, ...). One request per web link, plus a walk of the
            course's exercises.

    Returns:
        The leerpad's name, ``progress_percent`` and sections; each step
        has its ``module``
        (documents, weblinks, exercises, ...), ``item_id`` (the ``doc_id`` for
        documents), ``name``, ``done`` (finished by the pupil) and, when
        resolved, ``url`` (and ``provider`` for exercises). An exercise's url
        opens it in Smartschool, logged in as the pupil.
    """
    try:
        session = _session()
        found = _find_course(session, course)
        page = _lpaths_page(
            session,
            f"/Lpaths/Viewlpath/Index/courseID/{found.id}/lpathID/{learning_path_id}",
        )
        if page is None:
            return {"error": f"No leerpaden module in {found.name}"}
        result = content.parse_lpath_view(page)
        steps = [step for section in result["sections"] for step in section["steps"]]
        if resolve_links:
            exercises = (
                _course_exercises(session, found)
                if any(s["module"] == "exercises" for s in steps)
                else {}
            )
            for step in steps:
                if step["module"] == "weblinks" and step["item_id"]:
                    step["url"] = _weblink_url(session, found, step["item_id"])
                elif step["module"] == "exercises" and step["item_id"] in exercises:
                    exercise = exercises[step["item_id"]]
                    step["provider"] = exercise["provider"]
                    step["url"] = session.create_url(exercise["url"])
        return {
            "course": {"id": found.id, "name": found.name},
            "id": learning_path_id,
            **result,
            "total_steps": len(steps),
        }
    except Exception as e:
        return {"error": f"Failed to retrieve learning path: {e!s}"}


@mcp.tool()
def get_children() -> dict[str, Any]:
    """
    List the children linked on Mijn kinderen for this (parent) login.

    Use ``account_id`` with switch_child to change whose planner, results and
    messages the other tools return; the session stays on the selected child.

    When the user asks for a child that is not in ``children``, that child is
    not linked on Mijn kinderen and cannot be read from this login. Do not
    guess an id, and do not ask for a school, address, username or password.
    A listed child on another school needs no address either: switch_child
    follows the link and only asks for that child's birth date.

    Returns:
        Dictionary with ``children`` (account_id, name, first_name,
        last_name, class_name, is_current), ``current`` (the active user) and
        ``total``.
    """
    try:
        return children.list_children(_session())
    except Exception as e:
        return {"error": f"Failed to retrieve children: {e!s}"}


@mcp.tool()
def switch_child(account_id: str, birth_date: str | None = None) -> dict[str, Any]:
    """
    Switch the session to another child linked on Mijn kinderen.

    After a successful switch, get_schedule, get_planned_elements,
    get_results, get_messages and the other tools follow that child. A child
    on another school platform is reached through that school's one-time
    link; that school then asks for the birth date of the child. Pass it as
    ``birth_date``: it is sent once, never together with the password, and
    stored once accepted, so it is asked for only the first time.

    Args:
        account_id: ``account_id`` from get_children.
        birth_date: Birth date of that child, dd-mm-yyyy. Only needed for a
            child on another school when switch_child asks for it.

    Returns:
        Dictionary with ``ok``, the new ``current`` user, ``switched_host``
        when the child is on another school, and ``saved_birth_date`` when a
        new birth date was stored.
    """
    try:
        return children.switch(_session(), account_id, birth_date)
    except Exception as e:
        return {"error": f"Failed to switch child: {e!s}"}


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


def _parse_since(since: str | None) -> date | None:
    """Parse an ISO ``YYYY-MM-DD`` string; raise ``ValueError`` if malformed."""
    if not since:
        return None
    try:
        return date.fromisoformat(since)
    except ValueError:
        raise ValueError(f"since must be YYYY-MM-DD, got {since!r}") from None


def _lvs_unavailable(what: str, error: Exception) -> dict[str, Any]:
    """Error dict for an account whose Smartschool rejects the LVS request."""
    return {
        "error": f"Failed to retrieve {what}: the student file (LVS) is not "
        f"available for this account (Smartschool answered: {error!s})"
    }


# With include_details every returned note costs one extra request.
_MAX_NOTES_WITH_DETAILS = 50


@mcp.tool()
def get_student_notes(
    limit: int = 15,
    offset: int = 0,
    category: str | None = None,
    since: str | None = None,
    include_details: bool = False,
) -> dict[str, Any]:
    """
    Retrieve student file notes ("dossierlijnen") from the pupil-tracking
    system (Leerlingvolgsysteem / LVS), newest first.

    Notes marked confidential (private) by the school are never returned; only
    their number is reported under ``filters.private_hidden``.

    Args:
        limit: Maximum number of notes to return (default: 15, minimum 0).
            With include_details the maximum is 50.
        offset: Number of notes to skip from the beginning (default: 0)
        category: Only notes whose category name contains this text
            (case-insensitive), e.g. "Mededeling"
        since: Only notes dated on or after this day, format YYYY-MM-DD
        include_details: Also fetch the content of each returned note (one
            extra request per note, default: False). For announcements the
            text is returned as plain text, not HTML; ``fields`` holds the
            other labelled values (e.g. the teacher). ``details`` also holds
            ``choice`` (the label chosen in the note's select field, e.g. the
            measure of an order measure; "" when there is none) and
            ``attachments`` (a list of ``{"name", "mime"}``; file contents are
            not available). A note whose details
            cannot be fetched gets ``details: null`` plus ``details_error``;
            their number is reported under ``filters.details_failed``.

    Returns:
        Dictionary with notes list, pagination info and applied filters.
        ``pagination.limit`` is the limit actually applied.

    Examples:
        - get_student_notes() -> Latest 15 notes (headers only)
        - get_student_notes(category="Te laat") -> Only that category
        - get_student_notes(since="2026-09-01", include_details=True) -> Notes
          since 1 September, with their content
    """
    try:
        since_date = _parse_since(since)
        session = _session()

        limit = max(0, limit)
        offset = max(0, offset)
        if include_details:
            limit = min(limit, _MAX_NOTES_WITH_DETAILS)

        visible = []
        private_hidden = 0
        for note in StudentNotes(session, since=since_date):
            if note.private:
                private_hidden += 1
                continue
            if category and category.lower() not in (note.cat_name or "").lower():
                continue
            if since_date and note.date.date() < since_date:
                continue
            visible.append(note)

        visible.sort(key=lambda n: n.date, reverse=True)

        end_index = offset + limit
        notes_list = []
        details_failed = 0
        for note in visible[offset:end_index]:
            note_data: dict[str, Any] = {
                "id": note.item_id,
                "category": note.cat_name,
                "title": note.title,
                "date": _safe_format_date(note.date),
                "author": note.author,
                "viewed": note.viewed,
                "school_year": note.school_year,
            }
            if include_details:
                try:
                    detail = StudentNoteDetails(session, item_id=note.item_id).get()
                    note_data["details"] = {
                        "text": detail.text,
                        "fields": detail.fields,
                        "date": _safe_format_date(detail.date),
                        "choice": detail.choice,
                        "attachments": [
                            {"name": a.file_name, "mime": a.file_mime}
                            for a in detail.attachments
                        ],
                    }
                except Exception as e:
                    details_failed += 1
                    note_data["details"] = None
                    note_data["details_error"] = f"{type(e).__name__}: {e!s}"
            notes_list.append(note_data)

        total = len(visible)
        filters: dict[str, Any] = {
            "category": category,
            "since": since,
            "include_details": include_details,
            "private_hidden": private_hidden,
        }
        if include_details:
            filters["details_failed"] = details_failed
            filters["max_limit_with_details"] = _MAX_NOTES_WITH_DETAILS
        return {
            "notes": notes_list,
            "pagination": {
                "limit": limit,
                "offset": offset,
                "total": total,
                "returned": len(notes_list),
                "has_more": bool(notes_list) and end_index < total,
            },
            "filters": filters,
        }

    except SmartSchoolRequestError as e:
        return _lvs_unavailable("student notes", e)
    except Exception as e:
        return {"error": f"Failed to retrieve student notes: {e!s}"}


@mcp.tool()
def get_student_absences(since: str | None = None) -> dict[str, Any]:
    """
    Retrieve recorded absences from the pupil-tracking system
    (Leerlingvolgsysteem / LVS), newest first.

    Args:
        since: Only absences on or after this day, format YYYY-MM-DD

    Returns:
        Dictionary with absences list (date, code, description, info, part of
        day "VM"/"NM", or null when not specified; school year) and the applied
        filters.

    Examples:
        - get_student_absences() -> All recorded absences
        - get_student_absences(since="2026-09-01") -> Absences since 1 September
    """
    try:
        since_date = _parse_since(since)

        absences = [
            a
            for a in StudentAbsences(_session(), since=since_date)
            if not (since_date and a.date < since_date)
        ]
        absences.sort(key=lambda a: a.date, reverse=True)

        absences_list = [
            {
                "date": _safe_format_date(a.date),
                "code": a.code,
                "description": a.description,
                "info": a.info,
                "part_of_day": a.part_of_day,
                "school_year": a.school_year,
            }
            for a in absences
        ]
        return {
            "absences": absences_list,
            "total": len(absences_list),
            "filters": {"since": since},
        }

    except SmartSchoolRequestError as e:
        return _lvs_unavailable("student absences", e)
    except Exception as e:
        return {"error": f"Failed to retrieve student absences: {e!s}"}


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
        attachments_list = [
            {
                "file_id": _attachment_file_id(att),
                "name": getattr(att, "name", "Unknown"),
                "mime_type": getattr(att, "mime", "Unknown"),
                "size": getattr(att, "size", "Unknown"),
            }
            for att in Attachments(_session(), msg_id=message_id)
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
    try:
        target_attachment = None
        for att in Attachments(_session(), msg_id=message_id):
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

        raw_name = getattr(target_attachment, "name", "") or ""
        filename = Path(raw_name).name or f"attachment_{file_id}"
        file_path = _save_download(resp.content, filename, save_path)

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

    ``smartschool.bs4_html`` is no longer exported (smartschool >= 0.12), so
    use BeautifulSoup directly.
    """
    from bs4 import BeautifulSoup

    raw_html = getattr(response, "text", response)
    return BeautifulSoup(raw_html, "html.parser")


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
    from urllib.parse import unquote

    try:
        session = _session()
        base = session.create_url("/")
        if not image_url.startswith(base):
            return {"error": f"Refusing to fetch a URL outside {base}"}

        resp = session.get(image_url)
        if not resp.ok:
            return {"error": f"Failed to download image: HTTP {resp.status_code}"}

        filename = Path(unquote(urlparse(image_url).path)).name or "homepage_image"
        file_path = _save_download(resp.content, filename, save_path)

        return {
            "name": file_path.name,
            "saved_to": str(file_path),
            "bytes_written": len(resp.content),
        }

    except Exception as e:
        return {"error": f"Failed to download image: {e!s}"}
