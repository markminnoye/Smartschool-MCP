"""Session setup and JSON output for the Smartschool Claude plugin scripts."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from smartschool import EnvCredentials, Smartschool

PLUGIN_ROOT = Path(__file__).resolve().parent.parent


def _quiet_library_logs() -> None:
    """The library logs the username on login. Scripts should print JSON only."""
    try:
        from logprise import logger
    except ImportError:
        return
    logger.disable("smartschool")


_quiet_library_logs()


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

    for key, value in parse_env_file(chosen.read_text(encoding="utf-8")).items():
        os.environ.setdefault(key, value)
    return chosen


def open_session() -> Smartschool:
    """One Smartschool session from the environment (after loading config.env)."""
    load_config()
    credentials = EnvCredentials()
    credentials.validate()
    return Smartschool(credentials)


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
