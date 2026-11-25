"""Tests for the planner fallback of get_schedule and get_future_tasks."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import smartschool_mcp.server as srv


def _element(
    element_type: str,
    start: datetime,
    *,
    course: str | None = "Wiskunde",
    name: str = "",
    assignment_type: str | None = None,
    room: str | None = "v102",
) -> SimpleNamespace:
    return SimpleNamespace(
        id="00000000-0000-0000-0000-000000000001",
        planned_element_type=element_type,
        period=SimpleNamespace(
            date_time_from=start, date_time_to=start + timedelta(minutes=50)
        ),
        courses=[SimpleNamespace(name=course)] if course else [],
        name=name,
        color="blue",
        assignment_type=SimpleNamespace(name=assignment_type)
        if assignment_type
        else None,
        locations=[SimpleNamespace(title=room)] if room else [],
        organisers=SimpleNamespace(users=[]),
    )


def _at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute)


# ── get_schedule ─────────────────────────────────────────────────────────────


def test_get_schedule_keeps_agenda_lessons_without_reading_the_planner() -> None:
    lesson = MagicMock(date=date.today(), hour="1")
    with (
        patch("smartschool_mcp.server.SmartschoolLessons", return_value=[lesson]),
        patch("smartschool_mcp.server.PlannedElements") as planner,
    ):
        result = srv.get_schedule()

    assert result["source"] == "agenda"
    assert result["total"] == 1
    planner.assert_not_called()


def test_get_schedule_reads_the_planner_when_the_agenda_is_empty() -> None:
    today = date.today()
    elements = [
        _element("planned-placeholders", _at(today, 9, 15), course="Fysica"),
        _element("planned-lessons", _at(today, 8, 25), name="Thema 2"),
        # Another day: the planner's window can spill over midnight.
        _element("planned-lessons", _at(today + timedelta(days=1), 8, 25)),
    ]
    with (
        patch("smartschool_mcp.server.SmartschoolLessons", return_value=[]),
        patch("smartschool_mcp.server.PlannedElements", return_value=elements) as pl,
    ):
        result = srv.get_schedule()

    assert result["source"] == "planner"
    assert [lesson["hour"] for lesson in result["lessons"]] == [
        "08:25-09:15",
        "09:15-10:05",
    ]
    first = result["lessons"][0]
    assert first["course"] == "Wiskunde"
    assert first["classroom"] == "v102"
    assert first["subject"] == "Thema 2"
    assert first["type"] == "planned-lessons"
    assert set(pl.call_args.kwargs["element_types"]) == set(srv._PLANNER_LESSON_TYPES)


def test_get_schedule_reports_a_planner_failure_next_to_the_empty_agenda() -> None:
    with (
        patch("smartschool_mcp.server.SmartschoolLessons", return_value=[]),
        patch(
            "smartschool_mcp.server.PlannedElements",
            side_effect=TypeError("unexpected keyword argument 'element_types'"),
        ),
    ):
        result = srv.get_schedule()

    assert "error" not in result
    assert result["source"] == "agenda"
    assert result["lessons"] == []
    assert "element_types" in result["planner_error"]


# ── get_future_tasks ─────────────────────────────────────────────────────────


def test_get_future_tasks_reads_the_planner_when_the_agenda_is_empty() -> None:
    monday = date.today() + timedelta(days=2)
    elements = [
        _element(
            "planned-assignments",
            _at(monday + timedelta(days=1), 8),
            name="Hoofdstuk 4",
            assignment_type="Schriftelijke toets",
        ),
        _element(
            "planned-assignments",
            _at(monday, 10),
            course="Chemie",
            name="Oefeningen",
            assignment_type="Huistaak",
        ),
        _element("planned-to-dos", _at(monday, 12), course=None, name="Brief"),
    ]
    with (
        patch("smartschool_mcp.server.FutureTasks", return_value=[]),
        patch("smartschool_mcp.server.PlannedElements", return_value=elements) as pl,
    ):
        result = srv.get_future_tasks()

    assert result["source"] == "planner"
    assert result["total_days"] == 2
    assert result["total_tasks"] == 3
    first_day = result["future_tasks"][0]
    assert first_day["date"] == monday.strftime("%Y-%m-%d")
    assert first_day["courses"] == [
        {
            "name": "Chemie",
            "tasks": [
                {"label": "Huistaak", "description": "Oefeningen", "warning": False}
            ],
        },
        {
            "name": "-",
            "tasks": [{"label": "To-do", "description": "Brief", "warning": False}],
        },
    ]
    assert set(pl.call_args.kwargs["element_types"]) == set(srv._PLANNER_TASK_TYPES)


def test_get_future_tasks_reports_a_planner_failure_next_to_the_empty_agenda() -> None:
    with (
        patch("smartschool_mcp.server.FutureTasks", return_value=[]),
        patch("smartschool_mcp.server.PlannedElements", side_effect=RuntimeError("x")),
    ):
        result = srv.get_future_tasks()

    assert "error" not in result
    assert result["source"] == "agenda"
    assert result["total_tasks"] == 0
    assert result["planner_error"] == "x"
