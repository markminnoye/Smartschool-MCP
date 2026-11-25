"""Feedback attachments that arrive as objects must not break get_results.

``smartschool`` types ``FeedbackFull.attachments`` as ``list[str]``, but some
schools send ``[{"id": "<uuid>", "wopiUrl": null}, ...]``. One such feedback
failed validation of the whole results page, so get_results returned an error
instead of any grade.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import smartschool_mcp.server as srv


def _evaluation_payload(
    *, color: str, name: str, description: str
) -> dict[str, object]:
    """Minimal ``/results/api/v1/evaluations/`` row (camelCase, as on the wire)."""
    person = {
        "id": "1",
        "pictureHash": "h",
        "pictureUrl": "https://example.be/a",
        "description": {"startingWithFirstName": "", "startingWithLastName": ""},
        "name": {
            "startingWithFirstName": "Anna Jansen",
            "startingWithLastName": "Jansen Anna",
        },
        "sort": "j",
        "deleted": False,
    }
    school_class = {
        "identifier": "1",
        "id": 1,
        "platformId": 1,
        "name": "1B",
        "type": "K",
        "icon": "b",
    }
    year = {
        "id": 1,
        "dateRange": {
            "start": "2025-09-01T00:00:00+02:00",
            "end": "2026-08-31T00:00:00+02:00",
        },
    }
    return {
        "identifier": f"eval-{name}",
        "type": "normal",
        "name": name,
        "graphic": {
            "type": "percentage",
            "color": color,
            "value": 80,
            "description": description,
        },
        "date": "2026-03-11T00:00:00+01:00",
        "gradebookOwner": person,
        "component": None,
        "courses": [
            {
                "id": 101,
                "name": "Wiskunde",
                "graphic": {"type": "icon", "value": "calc"},
                "teachers": [],
                "class": school_class,
                "skoreClassId": 1,
                "parentCourseId": None,
                "skoreWorkYear": year,
            }
        ],
        "period": {
            "id": 1,
            "name": "Period 1",
            "icon": "i",
            "skoreWorkYear": year,
            "isActive": True,
            "class": school_class,
        },
        "feedback": [],
        "feedbacks": [],
        "availabilityDate": "2026-03-11T00:00:00+01:00",
        "isPublished": True,
        "doesCount": True,
    }


def _feedback_with_object_attachments(row: dict[str, Any]) -> dict[str, Any]:
    """De Pass: feedback attachments arrive as objects, not as id strings."""
    person = row["gradebookOwner"]
    row["feedbacks"] = [
        {
            "attachments": [
                {"id": "242e6d17-2a8a-4a6e-9c1b-0000000cf7fd", "wopiUrl": None},
                {"id": "58ece3d2-74a8-4420-8f1c-000000096d33", "wopiUrl": None},
                "plain-id-still-works",
            ],
            "changedAt": "2026-11-17T10:00:00+01:00",
            "createdAt": "2026-11-17T10:00:00+01:00",
            "evaluationId": row["identifier"],
            "id": "fb-1",
            "student": person,
            "teacher": person,
            "text": "Goed gedaan",
        }
    ]
    row["feedback"] = [{"text": "Goed gedaan", "user": person}]
    return row


def test_get_results_accepts_feedback_attachment_objects(
    mock_session: MagicMock,
) -> None:
    """One feedback with ``{"id", "wopiUrl"}`` attachments failed every call."""
    mock_session.json.return_value = [
        _feedback_with_object_attachments(
            _evaluation_payload(color="green", name="Met bijlagen", description="9/10")
        ),
        _evaluation_payload(color="green", name="Zonder bijlagen", description="7/10"),
    ]

    result = srv.get_results(include_details=False)

    assert "error" not in result, result
    by_name = {row["assignment"]: row for row in result["results"]}
    assert set(by_name) == {"Met bijlagen", "Zonder bijlagen"}
    assert by_name["Met bijlagen"]["feedback"] == "Goed gedaan"
    assert by_name["Met bijlagen"]["feedback_attachment_ids"] == [
        "242e6d17-2a8a-4a6e-9c1b-0000000cf7fd",
        "58ece3d2-74a8-4420-8f1c-000000096d33",
        "plain-id-still-works",
    ]
    assert by_name["Zonder bijlagen"]["feedback_attachment_ids"] == []


def test_feedback_patch_reaches_every_model_that_embeds_it() -> None:
    from smartschool._objects import FeedbackFull, ProjectGoal, Result, ResultDetails

    from smartschool_mcp.result_feedback import attachment_ids

    for cls in (FeedbackFull, ProjectGoal, ResultDetails, Result):
        assert "attachment_ids" in repr(cls.__pydantic_core_schema__), cls
    assert attachment_ids([{"id": "a", "wopiUrl": None}, "b", {"wopiUrl": "x"}]) == [
        "a",
        "b",
    ]
