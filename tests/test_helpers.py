"""Unit tests for pure helper functions in server.py."""

from __future__ import annotations

from datetime import date, datetime
from unittest.mock import MagicMock

from smartschool_mcp.server import (
    _as_child_records,
    _person_dict,
    _safe_account_id,
    _safe_format_date,
    _safe_get_teacher_names,
)

# ── _safe_format_date ─────────────────────────────────────────────────────────


def test_safe_format_date_returns_none_for_none() -> None:
    assert _safe_format_date(None) is None


def test_safe_format_date_with_date_object() -> None:
    assert _safe_format_date(date(2024, 9, 1)) == "2024-09-01"


def test_safe_format_date_with_datetime_object() -> None:
    assert _safe_format_date(datetime(2024, 9, 1, 8, 30)) == "2024-09-01"


def test_safe_format_date_with_string_returns_none() -> None:
    # Strings don't have strftime → AttributeError → None
    assert _safe_format_date("2024-09-01") is None  # type: ignore[arg-type]


def test_safe_format_date_with_integer_returns_none() -> None:
    assert _safe_format_date(12345) is None  # type: ignore[arg-type]


# ── _safe_get_teacher_names ───────────────────────────────────────────────────


def test_safe_get_teacher_names_empty_list() -> None:
    assert _safe_get_teacher_names([]) == []


def test_safe_get_teacher_names_none() -> None:
    assert _safe_get_teacher_names(None) == []


def test_safe_get_teacher_names_uses_last_name_by_default() -> None:
    teacher = MagicMock()
    teacher.name.starting_with_last_name = "Doe, John"
    result = _safe_get_teacher_names([teacher])
    assert result == ["Doe, John"]


def test_safe_get_teacher_names_uses_first_name_when_requested() -> None:
    teacher = MagicMock()
    teacher.name.starting_with_first_name = "John Doe"
    result = _safe_get_teacher_names([teacher], use_last_name=False)
    assert result == ["John Doe"]


def test_safe_get_teacher_names_multiple_teachers() -> None:
    teachers = [MagicMock(), MagicMock()]
    teachers[0].name.starting_with_last_name = "Doe, John"
    teachers[1].name.starting_with_last_name = "Smith, Jane"
    result = _safe_get_teacher_names(teachers)
    assert result == ["Doe, John", "Smith, Jane"]


def test_safe_get_teacher_names_handles_attribute_error() -> None:
    teacher = MagicMock(spec=[])  # no attributes
    result = _safe_get_teacher_names([teacher])
    assert result == []


# ── Mijn kinderen helpers ─────────────────────────────────────────────────────


def test_as_child_records_unwraps_students_key() -> None:
    records = _as_child_records({"students": [{"accountID": 1}, "skip"]})
    assert records == [{"accountID": 1}]


def test_as_child_records_accepts_a_bare_list() -> None:
    assert _as_child_records([{"accountID": 1}, {"accountID": 2}]) == [
        {"accountID": 1},
        {"accountID": 2},
    ]


def test_person_dict_maps_studentcard_fields() -> None:
    mapped = _person_dict(
        {
            "accountID": 111,
            "firstName": "Elliot",
            "lastName": "Minnoye",
            "className": "1bb",
            "platform": "depass.smartschool.be",
        }
    )
    assert mapped["account_id"] == "111"
    assert mapped["name"] == "Elliot Minnoye"
    assert mapped["class_name"] == "1bb"
    assert mapped["platform"] == "depass.smartschool.be"


def test_safe_account_id_rejects_path_injection() -> None:
    assert _safe_account_id("12345") == "12345"
    assert _safe_account_id("49_10880_2") == "49_10880_2"
    assert _safe_account_id("../etc/passwd") is None
    assert _safe_account_id("1/2") is None
    assert _safe_account_id("") is None
