"""Tests for MCP tool registration and error-handling behaviour."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from smartschool import Attachment
from smartschool._objects import GraphicColor, PercentageGraphic, TextGraphic

import smartschool_mcp.server as srv

# ── Tool registration ─────────────────────────────────────────────────────────

EXPECTED_TOOLS = [
    "get_courses",
    "get_results",
    "get_future_tasks",
    "get_messages",
    "get_schedule",
    "get_periods",
    "get_reports",
    "get_planned_elements",
    "get_planner_attachments",
    "download_planner_file",
    "get_course_documents",
    "download_course_document",
    "get_student_support_links",
    "get_children",
    "switch_child",
    "get_homepage_blocks",
    "download_homepage_image",
]


def test_all_tools_are_defined() -> None:
    for name in EXPECTED_TOOLS:
        assert hasattr(srv, name), f"Tool {name!r} not found in server module"
        assert callable(getattr(srv, name))


def test_mcp_server_name() -> None:
    assert srv.mcp.name == "Smartschool MCP"


# ── Error-handling: every tool must catch exceptions ─────────────────────────


def test_get_courses_returns_error_on_exception() -> None:
    with patch("smartschool_mcp.server.Courses", side_effect=RuntimeError("network")):
        result = srv.get_courses()
    assert isinstance(result, list)
    assert "error" in result[0]
    assert "network" in result[0]["error"]


def test_get_results_returns_error_on_exception() -> None:
    with patch("smartschool_mcp.server.Results", side_effect=RuntimeError("auth")):
        result = srv.get_results()
    assert isinstance(result, dict)
    assert "error" in result
    assert "auth" in result["error"]


def test_get_future_tasks_returns_error_on_exception() -> None:
    with (
        patch(
            "smartschool_mcp.server.FutureTasks", side_effect=RuntimeError("timeout")
        ),
        patch(
            "smartschool_mcp.server.PlannedElements",
            side_effect=RuntimeError("planner down"),
        ),
    ):
        result = srv.get_future_tasks()
    assert isinstance(result, dict)
    assert "error" in result
    assert "timeout" in result["error"]


def test_get_messages_returns_error_on_exception() -> None:
    with patch(
        "smartschool_mcp.server.MessageHeaders", side_effect=RuntimeError("403")
    ):
        result = srv.get_messages()
    assert isinstance(result, dict)
    assert "error" in result


def test_get_schedule_returns_error_on_exception(mock_session: MagicMock) -> None:
    mock_session.authenticated_user = {"id": "49_1_2"}
    mock_session.json.side_effect = RuntimeError("503")
    result = srv.get_schedule()
    assert isinstance(result, dict)
    assert "error" in result


def test_get_periods_returns_error_on_exception() -> None:
    with patch("smartschool_mcp.server.Periods", side_effect=RuntimeError("oops")):
        result = srv.get_periods()
    assert isinstance(result, list)
    assert "error" in result[0]


def test_get_reports_returns_error_on_exception() -> None:
    with patch("smartschool_mcp.server.Reports", side_effect=RuntimeError("oops")):
        result = srv.get_reports()
    assert isinstance(result, list)
    assert "error" in result[0]


def test_get_planned_elements_returns_error_on_exception(
    mock_session: MagicMock,
) -> None:
    mock_session.authenticated_user = {"id": "49_1_2"}
    mock_session.json.side_effect = RuntimeError("oops")
    result = srv.get_planned_elements()
    assert isinstance(result, dict)
    assert "error" in result


_ELEMENT_ID = "0a13e756-656a-5053-925e-7aad5db31a87"


def _planner_raw(**overrides: object) -> dict:
    raw: dict = {
        "id": _ELEMENT_ID,
        "platformId": 49,
        "name": "Wiskunde",
        "plannedElementType": "planned-lessons",
        "period": {
            "dateTimeFrom": "2026-09-17T08:25:00+02:00",
            "dateTimeTo": "2026-09-17T09:15:00+02:00",
            "wholeDay": False,
        },
        "color": "#ffcc00",
        "courses": [{"name": "Wiskunde"}],
        "locations": [{"title": "A12"}],
        "organisers": {"users": [{"name": {"startingWithFirstName": "Jan Jansen"}}]},
        "unconfirmed": False,
        "pinned": False,
        "publicInfo": "<p>Neem je boek mee</p>",
        "uploadFolders": [
            {
                "id": "folder-1",
                "name": "Bijlagen",
                "files": [
                    {
                        "id": "file-1",
                        "filename": "oefening.pdf",
                        "mimeType": "application/pdf",
                        "fileSize": 1200,
                        "downloadUrl": "/planner/api/v1/files/file-1/download",
                    }
                ],
            }
        ],
    }
    raw.update(overrides)
    return raw


def test_get_schedule_returns_planner_elements(mock_session: MagicMock) -> None:
    mock_session.authenticated_user = {"id": "49_1_2"}
    mock_session.json.return_value = [_planner_raw()]
    result = srv.get_schedule(date_offset=0)

    assert result["total"] == 1
    assert "lessons" not in result
    item = result["elements"][0]
    assert item["id"] == _ELEMENT_ID
    assert item["type"] == "planned-lessons"
    assert item["name"] == "Wiskunde"
    assert item["description"] == "Neem je boek mee"
    assert item["courses"] == ["Wiskunde"]
    assert item["locations"] == ["A12"]
    assert item["organisers"] == ["Jan Jansen"]
    assert item["from"] == "2026-09-17 08:25"
    assert item["upload_folders"][0]["files"][0]["has_download_url"] is True
    assert "download_url" not in item["upload_folders"][0]["files"][0]
    args, kwargs = mock_session.json.call_args
    assert kwargs["data"]["from"] == kwargs["data"]["to"]
    assert "types" not in kwargs["data"]
    assert "includes" not in kwargs["data"]
    assert "/planner/api/v1/planned-elements/user/49_1_2" in args[0]


def test_get_planned_elements_omits_types_by_default(mock_session: MagicMock) -> None:
    mock_session.authenticated_user = {"id": "49_1_2"}
    mock_session.json.return_value = [_planner_raw()]
    result = srv.get_planned_elements(days_ahead=7)

    assert result["total"] == 1
    assert result["planned_elements"][0]["type"] == "planned-lessons"
    assert result["planned_elements"][0]["id"] == _ELEMENT_ID
    data = mock_session.json.call_args.kwargs["data"]
    assert "types" not in data
    assert "includes" not in data


def test_calendar_element_reads_free_text_and_room_locations() -> None:
    from smartschool_mcp.planner_fields import calendar_element_dict

    raw = _planner_raw(
        locations=[
            {"type": "mini-db-item", "title": "A12"},
            {"type": "text", "text": "thuis"},
            {"type": "mystery"},
        ]
    )
    row = calendar_element_dict(raw)
    assert row["locations"] == ["A12", "thuis"]


def test_planned_element_model_rejects_raw_calendar_fixture() -> None:
    """0.13.0 PlannedElement requires fields the website calendar omits.

    get_schedule therefore keeps the raw JSON parser in planner_fields.
    """
    from pydantic import ValidationError
    from smartschool import PlannedElement

    with pytest.raises(ValidationError):
        PlannedElement(**_planner_raw())


def test_get_planned_elements_passes_types_and_includes(
    mock_session: MagicMock,
) -> None:
    mock_session.authenticated_user = {"id": "49_1_2"}
    mock_session.json.return_value = []
    srv.get_planned_elements(
        from_date="2026-09-17",
        to_date="2026-09-21",
        types="planned-assignments,planned-to-dos",
        includes="icon,courses",
    )

    data = mock_session.json.call_args.kwargs["data"]
    assert data["types"] == "planned-assignments,planned-to-dos"
    assert data["includes"] == "icon,courses"
    assert data["from"] == "2026-09-17"
    assert data["to"] == "2026-09-21"


def test_get_planner_attachments_lists_files(mock_session: MagicMock) -> None:
    mock_session.authenticated_user = {"id": "49_1_2"}
    mock_session.json.return_value = [_planner_raw()]
    result = srv.get_planner_attachments(
        _ELEMENT_ID, from_date="2026-09-17", to_date="2026-09-17"
    )

    assert result["element_id"] == _ELEMENT_ID
    assert result["description"] == "Neem je boek mee"
    assert result["upload_folders"][0]["name"] == "Bijlagen"
    assert result["live_verified"] is False
    assert result["detail_fetch"]["status"] == "not_called"
    assert (
        mock_session.json.call_args.kwargs["data"]["includes"]
        == "icon,courses,locations,upload-folders,labels"
    )


def test_get_planner_attachments_rejects_bad_id() -> None:
    result = srv.get_planner_attachments("../etc")
    assert result == {"error": "Invalid element_id"}


def test_get_planner_attachments_merges_unverified_assignment_detail(
    mock_session: MagicMock,
) -> None:
    mock_session.authenticated_user = {"id": "49_1_2"}
    calendar = _planner_raw(
        plannedElementType="planned-assignments",
        publicInfo="",
        uploadFolders=[],
    )
    detail = _planner_raw(description="Hoofdstuk 3", publicInfo="")

    def fake_json(path: str, **kwargs: object) -> object:
        if path.startswith("/planner/api/v1/planned-assignments/"):
            return detail
        return [calendar]

    mock_session.json.side_effect = fake_json
    result = srv.get_planner_attachments(
        _ELEMENT_ID, from_date="2026-09-17", to_date="2026-09-17"
    )
    assert result["description"] == "Hoofdstuk 3"
    assert result["detail_fetch"]["status"] == "parsed"
    assert result["detail_fetch"]["body_verified"] is False


def test_download_planner_file_writes_bytes(mock_session: MagicMock, tmp_path) -> None:
    mock_session.authenticated_user = {"id": "49_1_2"}
    mock_session.json.return_value = [_planner_raw()]
    mock_session.get.return_value = MagicMock(
        ok=True, content=b"pdf-bytes", status_code=200
    )
    result = srv.download_planner_file(
        _ELEMENT_ID,
        "file-1",
        from_date="2026-09-17",
        to_date="2026-09-17",
        save_path=str(tmp_path),
    )
    assert result["bytes_written"] == 9
    assert result["live_verified"] is False
    assert (tmp_path / "oefening.pdf").read_bytes() == b"pdf-bytes"
    assert mock_session.get.call_args.args[0] == "/planner/api/v1/files/file-1/download"


def test_download_planner_file_refuses_foreign_url(mock_session: MagicMock) -> None:
    raw = _planner_raw()
    raw["uploadFolders"][0]["files"][0]["downloadUrl"] = "https://evil.example/file"
    mock_session.authenticated_user = {"id": "49_1_2"}
    mock_session.create_url.return_value = "https://school.smartschool.be/"
    mock_session.json.return_value = [raw]
    mock_session.get.return_value = MagicMock(ok=False, content=b"", status_code=404)
    result = srv.download_planner_file(
        _ELEMENT_ID, "file-1", from_date="2026-09-17", to_date="2026-09-17"
    )
    assert "geen werkend downloadpad" in result["error"]
    assert result["live_verified"] is False
    called = " ".join(str(call.args) for call in mock_session.get.call_args_list)
    assert "evil.example" not in called
    assert "/planner/api/v1/files/file-1/download" in result["tried"]
    assert (
        f"/planner/api/v1/planned-assignments/49/{_ELEMENT_ID}/attachments/file-1"
    ) in result["tried"]
    assert (
        f"/planner/api/v1/planned-elements/{_ELEMENT_ID}/attachments/file-1/download"
    ) in result["tried"]


def test_download_planner_file_refuses_missing_url(mock_session: MagicMock) -> None:
    raw = _planner_raw()
    del raw["uploadFolders"][0]["files"][0]["downloadUrl"]
    mock_session.authenticated_user = {"id": "49_1_2"}
    mock_session.json.return_value = [raw]
    mock_session.get.return_value = MagicMock(
        ok=True,
        content=b"<!DOCTYPE html><html></html>",
        status_code=200,
        headers={"Content-Type": "text/html"},
    )
    result = srv.download_planner_file(
        _ELEMENT_ID, "file-1", from_date="2026-09-17", to_date="2026-09-17"
    )
    assert "geen werkend downloadpad" in result["error"]
    assert result["live_verified"] is False
    assert result["tried"]
    mock_session.get.assert_called()


def test_download_planner_file_stops_at_first_file(
    mock_session: MagicMock, tmp_path
) -> None:
    raw = _planner_raw()
    del raw["uploadFolders"][0]["files"][0]["downloadUrl"]
    mock_session.authenticated_user = {"id": "49_1_2"}
    mock_session.json.return_value = [raw]
    html = MagicMock(
        ok=True,
        content=b"<!DOCTYPE html><html></html>",
        status_code=200,
        headers={"Content-Type": "text/html"},
    )
    pdf = MagicMock(
        ok=True,
        content=b"%PDF",
        status_code=200,
        headers={"Content-Type": "application/pdf"},
    )
    mock_session.get.side_effect = [html, pdf]
    result = srv.download_planner_file(
        _ELEMENT_ID,
        "file-1",
        from_date="2026-09-17",
        to_date="2026-09-17",
        save_path=str(tmp_path),
    )
    assert result["bytes_written"] == 4
    assert result["download_path"] == (
        "/planner/api/v1/planned-assignments/49/"
        f"{_ELEMENT_ID}/attachments/file-1/download"
    )
    assert result["live_verified"] is False
    assert mock_session.get.call_count == 2
    assert (tmp_path / "oefening.pdf").read_bytes() == b"%PDF"


def test_get_planner_attachments_include_raw_hides_secrets(
    mock_session: MagicMock,
) -> None:
    raw = _planner_raw()
    raw["uploadFolders"][0]["files"][0]["sessionToken"] = "super-secret-cookie"
    raw["uploadFolders"][0]["files"][0]["downloadUrl"] = (
        "/planner/api/v1/files/file-1/download?token=abc"
    )
    mock_session.authenticated_user = {"id": "49_1_2"}
    mock_session.json.return_value = [raw]
    result = srv.get_planner_attachments(
        _ELEMENT_ID,
        from_date="2026-09-17",
        to_date="2026-09-17",
        include_raw=True,
    )
    blob = str(result)
    assert "super-secret-cookie" not in blob
    assert "token=abc" not in blob
    assert "uploadFolders" in result["raw"]["attachment"]["element_keys"]
    urls = result["raw"]["attachment"]["fields"]["uploadFolders"]["url_fields"]
    assert any(
        item["value"] == "/planner/api/v1/files/file-1/download" for item in urls
    )
    assert result["raw"]["detail"]["present"] is False


def test_get_planner_attachments_include_raw_shows_safe_attachment_values(
    mock_session: MagicMock,
) -> None:
    calendar = _planner_raw(
        plannedElementType="planned-assignments",
        publicInfo="",
        uploadFolders=[],
    )
    detail = {
        "id": _ELEMENT_ID,
        "attachments": [
            {
                "id": "att-1",
                "fileName": "klaslijst-geheim.pdf",
                "fileSize": 12,
                "mimeType": "application/pdf",
                "visibility": {"option": "after_end", "daysAfterEnd": 1},
            }
        ],
        "uploadFolder": {"id": "folder-9", "name": "Geheim map"},
    }

    def fake_json(path: str, **kwargs: object) -> object:
        if path.startswith("/planner/api/v1/planned-assignments/"):
            return detail
        return [calendar]

    mock_session.authenticated_user = {"id": "49_1_2"}
    mock_session.json.side_effect = fake_json
    result = srv.get_planner_attachments(
        _ELEMENT_ID,
        from_date="2026-09-17",
        to_date="2026-09-17",
        include_raw=True,
    )
    listed = result["upload_folders"]
    file_ids = [item["id"] for folder in listed for item in folder["files"]]
    assert file_ids == ["att-1"]
    raw_detail = result["raw"]["detail"]
    assert raw_detail["attachments"] == [
        {
            "path": "attachments[]",
            "id": "att-1",
            "mimeType": "application/pdf",
            "visibility": {"option": "after_end", "daysAfterEnd": 1},
        }
    ]
    blob = str(result["raw"])
    assert "klaslijst-geheim.pdf" not in blob
    assert "Geheim map" not in blob


def test_download_planner_file_tries_attachment_route_first(
    mock_session: MagicMock, tmp_path
) -> None:
    calendar = _planner_raw(
        plannedElementType="planned-assignments",
        publicInfo="",
        uploadFolders=[],
    )
    detail = {
        "id": _ELEMENT_ID,
        "attachments": [
            {
                "id": "att-1",
                "fileName": "oefening.pdf",
                "mimeType": "application/pdf",
                "fileSize": 4,
                "visibility": {"option": "visible", "daysAfterEnd": 0},
            }
        ],
        "uploadFolder": {"id": "folder-9"},
    }

    def fake_json(path: str, **kwargs: object) -> object:
        if path.startswith("/planner/api/v1/planned-assignments/"):
            return detail
        return [calendar]

    mock_session.authenticated_user = {"id": "49_1_2"}
    mock_session.json.side_effect = fake_json
    mock_session.get.return_value = MagicMock(
        ok=True,
        content=b"%PDF",
        status_code=200,
        headers={"Content-Type": "application/pdf"},
    )
    result = srv.download_planner_file(
        _ELEMENT_ID,
        "att-1",
        from_date="2026-09-17",
        to_date="2026-09-17",
        save_path=str(tmp_path),
    )
    expected = f"/planner/api/v1/planned-assignments/49/{_ELEMENT_ID}/attachments/att-1"
    assert result["download_path"] == expected
    assert result["live_verified"] is False
    assert mock_session.get.call_args_list[0].args[0] == expected
    assert all(
        call.args[0].startswith("/planner/") for call in mock_session.get.call_args_list
    )


def test_get_planner_attachments_returns_error_on_exception(
    mock_session: MagicMock,
) -> None:
    mock_session.authenticated_user = {"id": "49_1_2"}
    mock_session.json.side_effect = RuntimeError("offline")
    result = srv.get_planner_attachments(_ELEMENT_ID)
    assert "error" in result


def test_download_planner_file_returns_error_on_exception(
    mock_session: MagicMock,
) -> None:
    mock_session.authenticated_user = {"id": "49_1_2"}
    mock_session.json.side_effect = RuntimeError("offline")
    result = srv.download_planner_file(_ELEMENT_ID, "file-1")
    assert "error" in result


def test_get_course_documents_lists_topnav_courses(mock_session: MagicMock) -> None:
    course = MagicMock()
    course.id = 4496
    course.platform_id = 49
    course.name = "Wiskunde"
    course.teacher = "Jansen"
    with patch("smartschool_mcp.server.TopNavCourses", return_value=[course]):
        result = srv.get_course_documents()
    assert result["total"] == 1
    assert result["courses"][0]["id"] == 4496
    assert "get_courses" in result["note"]
    mock_session.json.assert_not_called()


def test_get_course_documents_lists_folder(mock_session: MagicMock) -> None:
    folder_row = MagicMock()
    folder_row.name = "H1"
    folder_row.link = None
    folder_row.browse_url = "/Documents/Index/Index/courseID/4496/parentID/10/ssID/49"
    folder_row.download_url = None
    file_row = MagicMock()
    file_row.name = "les.pdf"
    file_row.link = None
    file_row.browse_url = None
    file_row.download_url = (
        "/Documents/Download/Index/htm/0/courseID/4496/docID/8/ssID/49"
    )
    file_row.id = 8
    file_row.mime_type = "pdf"
    file_row.size_kb = 12.5
    file_row.last_modified = None
    file_row.view_url = None
    folder = MagicMock()
    folder.browse_url = "/Documents/Index/Index/courseID/4496/ssID/49"
    folder.items = [folder_row, file_row]
    with patch("smartschool_mcp.server.FolderItem", return_value=folder):
        result = srv.get_course_documents(course_id=4496, platform_id=49)
    assert result["total"] == 2
    assert result["items"][0]["kind"] == "folder"
    assert result["items"][1] == {
        "kind": "file",
        "id": 8,
        "name": "les.pdf",
        "mime_type": "pdf",
        "size_kb": 12.5,
        "last_modified": None,
        "view_url": None,
    }


def test_get_course_documents_rejects_foreign_browse_url() -> None:
    result = srv.get_course_documents(
        course_id=4496,
        platform_id=49,
        browse_url="/Documents/Index/Index/courseID/1/ssID/2",
    )
    assert "error" in result


def test_download_course_document_writes_file(
    mock_session: MagicMock, tmp_path
) -> None:
    file_row = MagicMock()
    file_row.name = "les.pdf"
    file_row.link = None
    file_row.browse_url = None
    file_row.download_url = (
        "/Documents/Download/Index/htm/0/courseID/4496/docID/8/ssID/49"
    )
    file_row.id = 8
    file_row.mime_type = "pdf"
    file_row.size_kb = 1
    file_row.last_modified = None
    file_row.view_url = None
    folder = MagicMock()
    folder.browse_url = "/Documents/Index/Index/courseID/4496/ssID/49"
    folder.items = [file_row]
    mock_session.get.return_value = MagicMock(ok=True, content=b"abc", status_code=200)
    with patch("smartschool_mcp.server.FolderItem", return_value=folder):
        result = srv.download_course_document(
            course_id=4496, document_id=8, platform_id=49, save_path=str(tmp_path)
        )
    assert result["bytes_written"] == 3
    assert (tmp_path / "les.pdf").read_bytes() == b"abc"


def test_download_course_document_adds_extension_from_mime(
    mock_session: MagicMock, tmp_path
) -> None:
    file_row = MagicMock()
    file_row.name = "werkblad"
    file_row.link = None
    file_row.browse_url = None
    file_row.download_url = (
        "/Documents/Download/Index/htm/0/courseID/4496/docID/8/ssID/49"
    )
    file_row.id = 8
    file_row.mime_type = "docx"
    file_row.size_kb = 1
    file_row.last_modified = None
    file_row.view_url = None
    folder = MagicMock()
    folder.browse_url = "/Documents/Index/Index/courseID/4496/ssID/49"
    folder.items = [file_row]
    mock_session.get.return_value = MagicMock(
        ok=True,
        content=b"docx",
        status_code=200,
        headers={},
    )
    with patch("smartschool_mcp.server.FolderItem", return_value=folder):
        result = srv.download_course_document(
            course_id=4496, document_id=8, platform_id=49, save_path=str(tmp_path)
        )
    assert result["name"] == "werkblad.docx"
    assert (tmp_path / "werkblad.docx").read_bytes() == b"docx"


def test_download_course_document_prefers_content_disposition(
    mock_session: MagicMock, tmp_path
) -> None:
    file_row = MagicMock()
    file_row.name = "werkblad"
    file_row.link = None
    file_row.browse_url = None
    file_row.download_url = (
        "/Documents/Download/Index/htm/0/courseID/4496/docID/8/ssID/49"
    )
    file_row.id = 8
    file_row.mime_type = "pdf"
    file_row.size_kb = 1
    file_row.last_modified = None
    file_row.view_url = None
    folder = MagicMock()
    folder.browse_url = "/Documents/Index/Index/courseID/4496/ssID/49"
    folder.items = [file_row]
    mock_session.get.return_value = MagicMock(
        ok=True,
        content=b"pdf",
        status_code=200,
        headers={"Content-Disposition": 'attachment; filename="opdracht.pdf"'},
    )
    with patch("smartschool_mcp.server.FolderItem", return_value=folder):
        result = srv.download_course_document(
            course_id=4496, document_id=8, platform_id=49, save_path=str(tmp_path)
        )
    assert result["name"] == "opdracht.pdf"
    assert not (tmp_path / "opdracht.pdf.pdf").exists()


def test_get_course_documents_returns_error_on_exception(
    mock_session: MagicMock,
) -> None:
    mock_session.authenticated_user = {"id": "1"}
    with patch(
        "smartschool_mcp.server.TopNavCourses", side_effect=RuntimeError("nope")
    ):
        result = srv.get_course_documents()
    assert "error" in result


def test_download_course_document_returns_error_on_exception() -> None:
    with patch(
        "smartschool_mcp.server.TopNavCourses", side_effect=RuntimeError("nope")
    ):
        result = srv.download_course_document(course_id=1, document_id=2, platform_id=3)
    assert "error" in result


def test_get_student_support_links_returns_error_on_exception() -> None:
    with patch(
        "smartschool_mcp.server.StudentSupportLinks", side_effect=RuntimeError("oops")
    ):
        result = srv.get_student_support_links()
    assert isinstance(result, list)
    assert "error" in result[0]


def test_get_children_returns_error_on_exception() -> None:
    with patch("smartschool_mcp.server._session", side_effect=RuntimeError("network")):
        result = srv.get_children()
    assert isinstance(result, dict)
    assert "error" in result
    assert "network" in result["error"]


def test_switch_child_returns_error_on_exception() -> None:
    with patch("smartschool_mcp.server._session", side_effect=RuntimeError("network")):
        result = srv.switch_child("12345")
    assert isinstance(result, dict)
    assert "error" in result


def test_switch_child_rejects_invalid_account_id() -> None:
    result = srv.switch_child("../etc/passwd")
    assert result == {"error": "Invalid account_id"}


def test_get_children_maps_studentcard_payload(mock_session: MagicMock) -> None:
    mock_session.get.return_value = MagicMock(ok=True, text="", url="/Studentcard")
    mock_session.json.return_value = [
        {
            "accountID": 111,
            "firstName": "Elliot",
            "lastName": "Minnoye",
            "className": "1bb",
        },
        {
            "accountID": 222,
            "firstName": "Other",
            "lastName": "Child",
            "className": "3a",
        },
    ]
    mock_session.authenticated_user = {
        "id": "49_1",
        "firstName": "Elliot",
        "lastName": "Minnoye",
        "username": "elliot.minnoye",
    }

    result = srv.get_children()

    mock_session.json.assert_called_once_with(
        "/Studentcard/Student/getStudents",
        method="post",
        headers={
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json",
        },
    )
    assert result["total"] == 2
    assert result["children"][0]["account_id"] == "111"
    assert result["children"][0]["name"] == "Elliot Minnoye"
    assert result["children"][0]["class_name"] == "1bb"
    assert result["current"]["name"] == "Elliot Minnoye"


def test_get_children_uses_topnav_id_when_account_id_is_zero(
    mock_session: MagicMock,
) -> None:
    mock_session.json.return_value = [
        {
            "accountID": 0,
            "userID": 11,
            "name": "Elliot",
            "surname": "Minnoye",
            "fullNameBIN": "Elliot Minnoye",
            "class": "1B-b ",
            "isCurrentUser": True,
        },
        {
            "accountID": 222,
            "userID": 22,
            "name": "Other",
            "surname": "Child",
            "fullNameBIN": "Other Child",
            "class": "3a",
            "isCurrentUser": False,
        },
    ]
    mock_session.get.return_value = MagicMock(
        ok=True,
        text=(
            '<a href="/Studentcard/Chain/gotourl/accountID/333" class="x">'
            "<img src='/p' alt='' /><span>Elliot</span></a>"
            '<a href="/Studentcard/Chain/gotourl/accountID/222" class="x">'
            "<span>Other</span></a>"
        ),
        url="/Studentcard",
    )
    mock_session.authenticated_user = {"id": "parent"}

    result = srv.get_children()

    elliot = result["children"][0]
    other = result["children"][1]
    assert elliot["account_id"] == "333"
    assert elliot["name"] == "Elliot Minnoye"
    assert elliot["class_name"] == "1B-b"
    assert elliot["is_current"] is True
    assert other["account_id"] == "222"
    assert other["is_current"] is False


def test_get_children_falls_back_to_topnav_when_json_fails(
    mock_session: MagicMock,
) -> None:
    mock_session.json.side_effect = RuntimeError("Failed to retrieve the json")
    mock_session.get.return_value = MagicMock(
        ok=True,
        text=(
            '<a href="/Studentcard/Chain/gotourl/accountID/333"><span>Elliot</span></a>'
        ),
        url="/Studentcard",
    )
    mock_session.authenticated_user = {"id": "parent"}

    result = srv.get_children()

    assert result["total"] == 1
    assert result["children"][0]["account_id"] == "333"
    assert result["children"][0]["first_name"] == "Elliot"


def test_switch_child_hits_gotourl_and_refreshes_user(mock_session: MagicMock) -> None:
    html = """
    <html><script>
    $.extend(true, SMSC, { vars : {"authenticatedUser": {
      "id": "49_2", "firstName": "Other", "lastName": "Child"
    }}});
    </script></html>
    """
    mock_session.get.return_value = MagicMock(
        ok=True,
        status_code=200,
        text=html,
        url="https://school.smartschool.be/",
        headers={},
    )
    mock_session.create_url.side_effect = lambda path: (
        f"https://school.smartschool.be{path}"
    )
    mock_session.creds = MagicMock()
    mock_session.creds.main_url = "school.smartschool.be"

    result = srv.switch_child("222")

    mock_session.get.assert_called_with(
        "/Studentcard/Chain/gotourl/accountID/222",
        allow_redirects=False,
    )
    assert result["ok"] is True
    assert result["account_id"] == "222"
    assert result["current"]["name"] == "Other Child"
    assert mock_session.authenticated_user["id"] == "49_2"


def test_switch_child_follows_cross_host_otp_redirect(
    mock_session: MagicMock,
) -> None:
    landing_html = """
    <html><script>
    $.extend(true, SMSC, { vars : {"authenticatedUser": {
      "id": "33_2", "firstName": "Other", "lastName": "Child"
    }}});
    </script></html>
    """
    gotourl = MagicMock(
        status_code=302,
        ok=False,
        text="",
        url="https://school.smartschool.be/Studentcard/Chain/gotourl/accountID/222",
        headers={"Location": "https://other.smartschool.be/otp/token"},
        history=[],
    )
    landing = MagicMock(
        status_code=200,
        ok=True,
        text=landing_html,
        url="https://other.smartschool.be/Studentcard",
        headers={},
        history=[],
    )

    def fake_get(url: str, **kwargs: object) -> MagicMock:
        if "gotourl" in str(url):
            return gotourl
        return landing

    mock_session.get.side_effect = fake_get
    mock_session.create_url.side_effect = lambda path: (
        f"https://{mock_session.creds.main_url}{path}"
        if str(path).startswith("/")
        else str(path)
    )
    mock_session.creds = MagicMock()
    mock_session.creds.main_url = "school.smartschool.be"

    result = srv.switch_child("222")

    assert result["ok"] is True
    assert result["switched_host"] == "other.smartschool.be"
    assert mock_session.creds.main_url == "other.smartschool.be"
    assert result["current"]["name"] == "Other Child"
    mock_session.get.assert_any_call(
        "https://other.smartschool.be/otp/token",
        allow_redirects=False,
    )


def test_switch_child_follows_relative_studentcard_after_otp(
    mock_session: MagicMock,
) -> None:
    landing_html = """
    <html><script>
    $.extend(true, SMSC, { vars : {"authenticatedUser": {
      "id": "33_2", "firstName": "Sufyan", "lastName": "Child"
    }}});
    </script></html>
    """
    gotourl = MagicMock(
        status_code=302,
        ok=False,
        text="",
        url="https://school.smartschool.be/Studentcard/Chain/gotourl/accountID/222",
        headers={"Location": "https://other.smartschool.be/otp/token"},
        history=[],
    )
    otp = MagicMock(
        status_code=302,
        ok=False,
        text="",
        url="https://other.smartschool.be/otp/token",
        headers={"Location": "/Studentcard"},
        history=[],
    )
    landing = MagicMock(
        status_code=200,
        ok=True,
        text=landing_html,
        url="https://other.smartschool.be/Studentcard",
        headers={},
        history=[],
    )

    def fake_get(url: str, **kwargs: object) -> MagicMock:
        if "gotourl" in str(url):
            return gotourl
        if "otp" in str(url):
            return otp
        if str(url).rstrip("/").endswith("/Studentcard"):
            return landing
        raise AssertionError(f"unexpected GET {url} {kwargs}")

    mock_session.get.side_effect = fake_get
    mock_session.create_url.side_effect = lambda path: (
        f"https://{mock_session.creds.main_url}{path}"
        if str(path).startswith("/")
        else str(path)
    )
    mock_session.creds = MagicMock()
    mock_session.creds.main_url = "school.smartschool.be"

    result = srv.switch_child("222")

    assert result["ok"] is True
    assert result["switched_host"] == "other.smartschool.be"
    assert result["current"]["name"] == "Sufyan Child"
    mock_session.get.assert_any_call(
        "https://other.smartschool.be/Studentcard",
        allow_redirects=False,
    )


def test_switch_child_does_not_submit_password_on_foreign_login(
    mock_session: MagicMock,
) -> None:
    gotourl = MagicMock(
        status_code=302,
        ok=False,
        text="",
        url="https://school.smartschool.be/Studentcard/Chain/gotourl/accountID/222",
        headers={"Location": "https://other.smartschool.be/otp/token"},
        history=[],
    )
    otp = MagicMock(
        status_code=302,
        ok=False,
        text="",
        url="https://other.smartschool.be/otp/token",
        headers={"Location": "https://other.smartschool.be/login"},
        history=[],
    )

    def fake_get(url: str, **kwargs: object) -> MagicMock:
        if "gotourl" in str(url):
            return gotourl
        if "otp" in str(url):
            return otp
        raise AssertionError(f"must not GET foreign login {url} {kwargs}")

    mock_session.get.side_effect = fake_get
    mock_session.create_url.side_effect = lambda path: (
        f"https://{mock_session.creds.main_url}{path}"
        if str(path).startswith("/")
        else str(path)
    )
    mock_session.creds = MagicMock()
    mock_session.creds.main_url = "school.smartschool.be"

    result = srv.switch_child("222")

    assert result["ok"] is False
    assert "login page" in result["error"]
    assert mock_session.creds.main_url == "school.smartschool.be"
    fetched = [str(call.args[0]) for call in mock_session.get.call_args_list]
    assert not any(srv._url_is_login(url) for url in fetched)
    assert not any(
        call.kwargs.get("allow_redirects") is True
        for call in mock_session.get.call_args_list
    )


def test_switch_child_blocks_when_foreign_verification_stays_on_auth(
    mock_session: MagicMock,
) -> None:
    gotourl = MagicMock(
        status_code=302,
        ok=False,
        text="",
        url="https://school.smartschool.be/Studentcard/Chain/gotourl/accountID/222",
        headers={"Location": "https://other.smartschool.be/otp/token"},
        history=[],
    )
    otp = MagicMock(
        status_code=302,
        ok=False,
        text="",
        url="https://other.smartschool.be/otp/token",
        headers={"Location": "https://other.smartschool.be/account-verification"},
        history=[],
    )
    verification = MagicMock(
        status_code=200,
        ok=True,
        text="<html><body>verify</body></html>",
        url="https://other.smartschool.be/account-verification",
        headers={},
        history=[],
    )

    def fake_get(url: str, **kwargs: object) -> MagicMock:
        if "gotourl" in str(url):
            return gotourl
        if "otp" in str(url):
            return otp
        if "account-verification" in str(url):
            return verification
        raise AssertionError(f"must not GET {url} {kwargs}")

    mock_session.get.side_effect = fake_get
    mock_session.create_url.side_effect = lambda path: (
        f"https://{mock_session.creds.main_url}{path}"
        if str(path).startswith("/")
        else str(path)
    )
    mock_session.creds = MagicMock()
    mock_session.creds.main_url = "school.smartschool.be"

    result = srv.switch_child("222")

    assert result["ok"] is False
    assert "password" in result["error"]
    fetched = [str(call.args[0]) for call in mock_session.get.call_args_list]
    assert any("account-verification" in url for url in fetched)
    assert not any(srv._url_is_login(url) for url in fetched)
    assert mock_session.creds.main_url == "school.smartschool.be"
    assert any(
        call.kwargs.get("allow_redirects") is True
        and "account-verification" in str(call.args[0])
        for call in mock_session.get.call_args_list
    )


# ── Happy-path: verify tool processes library objects correctly ───────────────


def test_get_courses_returns_list() -> None:
    mock_course = MagicMock()
    mock_course.name = "Wiskunde"
    mock_course.teachers = []

    with patch("smartschool_mcp.server.Courses", return_value=[mock_course]):
        result = srv.get_courses()

    assert result == [{"name": "Wiskunde", "teachers": []}]


def test_get_future_tasks_counts_tasks_correctly() -> None:
    """total_tasks must count tasks, not dict keys."""
    mock_task = MagicMock()
    mock_task.label = "Read chapter 3"
    mock_task.description = "Pages 40-60"
    mock_task.warning = False

    mock_items = MagicMock()
    mock_items.tasks = [mock_task, mock_task]  # 2 tasks

    mock_course = MagicMock()
    mock_course.course_title = "Biology"
    mock_course.items = mock_items

    mock_day = MagicMock()
    mock_day.date = None
    mock_day.courses = [mock_course]

    with (
        patch("smartschool_mcp.server.FutureTasks", return_value=[mock_day]),
        patch("smartschool_mcp.server.PlannedElements") as mock_planned,
    ):
        result = srv.get_future_tasks()

    assert result["total_tasks"] == 2  # was broken before (returned 4 = len(task_dict))
    mock_planned.assert_not_called()


def test_get_messages_invalid_box_type_is_an_error() -> None:
    with patch("smartschool_mcp.server.MessageHeaders", return_value=[]) as mock_mh:
        result = srv.get_messages(box_type="INVALID_BOX")
    assert "Unknown box_type" in result["error"]
    assert "INBOX" in result["error"]
    mock_mh.assert_not_called()


def test_get_messages_box_type_is_case_insensitive() -> None:
    with patch("smartschool_mcp.server.MessageHeaders", return_value=[]) as mock_mh:
        result = srv.get_messages(box_type=" sent ")
    assert "error" not in result
    assert mock_mh.call_args.kwargs["box_type"] is srv.BoxType.SENT


def test_get_results_pagination() -> None:
    mock_result = MagicMock()
    mock_result.courses = [MagicMock(name="Math")]
    mock_result.name = "Test 1"
    mock_result.graphic = MagicMock(
        description="8/10",
        value=8,
        achieved_points=8.0,
        total_points=10.0,
        percentage=0.8,
    )
    mock_result.date = None
    mock_result.availability_date = None
    mock_result.does_count = True
    mock_result.feedback = []
    mock_result.gradebook_owner = MagicMock()
    mock_result.gradebook_owner.name.starting_with_first_name = "John Doe"
    mock_result.period = MagicMock(name="Period 1")

    all_results = [mock_result] * 20

    with patch("smartschool_mcp.server.Results", return_value=all_results):
        result = srv.get_results(limit=5, offset=10, include_details=False)

    assert result["pagination"]["total"] == 20
    assert result["pagination"]["returned"] == 5
    assert result["pagination"]["offset"] == 10
    assert result["pagination"]["has_more"] is True


def test_get_results_with_details() -> None:
    mock_result = MagicMock()
    mock_result.courses = [MagicMock(name="Math")]
    mock_result.name = "Test 1"
    mock_result.graphic = MagicMock(
        description="8/10",
        value=8,
        achieved_points=8.0,
        total_points=10.0,
        percentage=0.8,
    )
    mock_result.date = None
    mock_result.availability_date = None
    mock_result.does_count = True
    mock_result.feedback = []
    mock_result.gradebook_owner = MagicMock()
    mock_result.gradebook_owner.name.starting_with_first_name = "John Doe"
    mock_result.period = MagicMock(name="Period 1")

    avg_tendency = MagicMock()
    avg_tendency.graphic.description = "7.5/10"
    avg_tendency.graphic.value = 7.5
    med_tendency = MagicMock()
    med_tendency.graphic.description = "7.8/10"
    med_tendency.graphic.value = 7.8
    mock_result.details.central_tendencies = [avg_tendency, med_tendency]

    with patch("smartschool_mcp.server.Results", return_value=[mock_result]):
        result = srv.get_results(limit=5, offset=0, include_details=True)

    assert result["results"][0]["average"] == {
        "description": "7.5/10",
        "value": 7.5,
    }
    assert result["results"][0]["median"] == {
        "description": "7.8/10",
        "value": 7.8,
    }
    assert "raw" not in result["results"][0]


def _grade_row(identifier: str, session: object) -> SimpleNamespace:
    return SimpleNamespace(
        courses=[SimpleNamespace(name="Wiskunde")],
        name="Toets",
        graphic=SimpleNamespace(
            description="8/10",
            value=8,
            achieved_points=8.0,
            total_points=10.0,
            percentage=0.8,
        ),
        date=None,
        availability_date=None,
        does_count=True,
        feedback=[],
        gradebook_owner=SimpleNamespace(
            name=SimpleNamespace(starting_with_first_name="Jan Jansen")
        ),
        period=SimpleNamespace(name="P1"),
        identifier=identifier,
        session=session,
    )


def test_get_results_parses_string_and_named_tendencies(
    mock_session: MagicMock,
) -> None:
    mock_session.json.return_value = {
        "identifier": "eval-1",
        "details": {
            "centralTendencies": [
                {"type": "median", "graphic": {"description": "7/10", "value": 7}},
                {"type": "average", "graphic": {"description": "6/10", "value": 6}},
            ],
            "teachers": [{"name": "Secret Teacher"}],
        },
    }
    with patch(
        "smartschool_mcp.server.Results",
        return_value=[_grade_row("eval-1", mock_session)],
    ):
        named = srv.get_results(include_details=True, include_raw=True)
    assert named["results"][0]["average"] == {"description": "6/10", "value": 6}
    assert named["results"][0]["median"] == {"description": "7/10", "value": 7}
    assert named["results"][0]["raw"]["numbers_found"] is True
    assert "Secret Teacher" not in str(named["results"][0]["raw"])

    mock_session.json.return_value = {
        "identifier": "eval-1",
        "details": {"centralTendencies": ["12/20", "11/20"]},
    }
    with patch(
        "smartschool_mcp.server.Results",
        return_value=[_grade_row("eval-1", mock_session)],
    ):
        strings = srv.get_results(include_details=True)
    assert strings["results"][0]["average"]["description"] == "12/20"
    assert strings["results"][0]["median"]["description"] == "11/20"
    assert "raw" not in strings["results"][0]


def test_get_results_keeps_null_when_school_hides_averages(
    mock_session: MagicMock,
) -> None:
    mock_session.json.return_value = {
        "identifier": "eval-1",
        "details": {
            "centralTendencies": [],
            "classAverage": {"description": "8/10", "value": 8},
            "mediaan": "7/10",
        },
    }
    second = _grade_row("eval-2", mock_session)
    with patch(
        "smartschool_mcp.server.Results",
        return_value=[_grade_row("eval-1", mock_session), second],
    ):
        hidden = srv.get_results(include_details=True, include_raw=True)
    # Alternate field names are used when the tendency list is empty.
    assert hidden["results"][0]["average"]["description"] == "8/10"
    assert hidden["results"][0]["median"]["description"] == "7/10"
    assert "raw" in hidden["results"][0]
    assert "raw" not in hidden["results"][1]

    mock_session.json.return_value = {
        "identifier": "eval-1",
        "details": {"centralTendencies": [], "teachers": []},
    }
    with patch(
        "smartschool_mcp.server.Results",
        return_value=[_grade_row("eval-1", mock_session)],
    ):
        empty = srv.get_results(include_details=True, include_raw=True)
    assert empty["results"][0]["average"] is None
    assert empty["results"][0]["median"] is None
    assert empty["results"][0]["raw"]["central_tendencies"]["length"] == 0
    assert empty["results"][0]["raw"]["numbers_found"] is False


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


def test_percentage_graphic_keeps_blue_and_unknown_colors() -> None:
    """0.13.0 GraphicColor lists blue; unknown names become ad-hoc members."""
    blue = PercentageGraphic(
        type="percentage", color="blue", value=80, description="8/10"
    )
    assert isinstance(blue.color, GraphicColor)
    assert blue.color == GraphicColor.BLUE

    purple = PercentageGraphic(
        type="percentage", color="purple", value=50, description="5/10"
    )
    assert isinstance(purple.color, GraphicColor)
    assert purple.color == "purple"

    green = PercentageGraphic(
        type="percentage", color="green", value=90, description="9/10"
    )
    assert isinstance(green.color, GraphicColor)
    assert green.color == GraphicColor.GREEN

    letter = TextGraphic(type="text", color="blue", value="B", description="letter")
    assert isinstance(letter.color, GraphicColor)
    assert letter.color == GraphicColor.BLUE


def test_get_results_accepts_blue_and_unknown_graphic_colors(
    mock_session: MagicMock,
) -> None:
    """One blue row used to fail the entire get_results call."""
    mock_session.json.return_value = [
        _evaluation_payload(color="blue", name="Toets blauw", description="8/10"),
        _evaluation_payload(color="purple", name="Toets paars", description="6/10"),
        _evaluation_payload(color="green", name="Toets groen", description="9/10"),
    ]

    result = srv.get_results(include_details=False)

    assert "error" not in result
    by_name = {row["assignment"]: row for row in result["results"]}
    assert set(by_name) == {"Toets blauw", "Toets paars", "Toets groen"}
    assert by_name["Toets blauw"]["course"] == "Wiskunde"
    assert by_name["Toets blauw"]["percentage"] == 0.8
    assert by_name["Toets blauw"]["score_description"] == "8/10"
    assert by_name["Toets paars"]["percentage"] == 0.6
    assert by_name["Toets groen"]["percentage"] == 0.9
    assert result["pagination"]["total"] == 3


def _planned_assignment(
    *,
    name: str,
    course: str,
    label: str,
    when: datetime,
    element_type: str = "planned-assignments",
) -> MagicMock:
    element = MagicMock()
    element.name = name
    element.planned_element_type = element_type
    element.period.date_time_from = when
    element.courses = [SimpleNamespace(name=course)]
    element.assignment_type = SimpleNamespace(name=label)
    return element


def test_get_future_tasks_falls_back_to_planned_assignments() -> None:
    """SR-94: empty Agenda list still returns Planner Toets/Huistaak rows."""
    today = date.today()
    friday = datetime.combine(today + timedelta(days=2), time(8, 25))
    later = datetime.combine(today + timedelta(days=6), time(10, 0))
    homework = datetime.combine(today + timedelta(days=2), time(13, 0))
    past = datetime.combine(today - timedelta(days=1), time(8, 0))
    elements = [
        _planned_assignment(
            name="Hoofdstuk 2 - Goed kijken",
            course="Wiskunde",
            label="Toets",
            when=friday,
        ),
        _planned_assignment(
            name="En classe",
            course="Frans",
            label="Toets",
            when=later,
        ),
        _planned_assignment(
            name="Hoofdstuk 3 - Schatten",
            course="Wiskunde",
            label="Huistaak (met punten)",
            when=homework,
        ),
        _planned_assignment(
            name="oude taak",
            course="Wiskunde",
            label="Toets",
            when=past,
        ),
        _planned_assignment(
            name="lesuur",
            course="Wiskunde",
            label="Les",
            when=friday,
            element_type="planned-lessons",
        ),
    ]

    with (
        patch("smartschool_mcp.server.FutureTasks", return_value=[]),
        patch(
            "smartschool_mcp.server.PlannedElements", return_value=elements
        ) as mock_planned,
    ):
        result = srv.get_future_tasks()

    assert "error" not in result
    assert result["total_days"] == 2
    assert result["total_tasks"] == 3
    first, second = result["future_tasks"]
    assert first["date"] == friday.date().isoformat()
    assert first["courses"] == [
        {
            "name": "Wiskunde",
            "tasks": [
                {
                    "label": "Toets",
                    "description": "Hoofdstuk 2 - Goed kijken",
                    "warning": False,
                },
                {
                    "label": "Huistaak (met punten)",
                    "description": "Hoofdstuk 3 - Schatten",
                    "warning": False,
                },
            ],
        }
    ]
    assert second["date"] == later.date().isoformat()
    assert second["courses"][0]["name"] == "Frans"
    assert second["courses"][0]["tasks"][0]["description"] == "En classe"
    kwargs = mock_planned.call_args.kwargs
    assert kwargs["element_types"] == "planned-assignments"
    assert "types" not in kwargs
    assert kwargs["from_date"] == today
    assert kwargs["till_date"] == today + timedelta(days=srv._FUTURE_TASKS_HORIZON_DAYS)


def test_get_future_tasks_keeps_empty_agenda_when_planner_fails() -> None:
    with (
        patch("smartschool_mcp.server.FutureTasks", return_value=[]),
        patch(
            "smartschool_mcp.server.PlannedElements",
            side_effect=RuntimeError("planner down"),
        ),
    ):
        result = srv.get_future_tasks()

    assert result == {"future_tasks": [], "total_days": 0, "total_tasks": 0}


def test_get_future_tasks_falls_back_when_agenda_raises() -> None:
    when = datetime.combine(date.today() + timedelta(days=1), time(9, 0))
    element = _planned_assignment(
        name="Hoofdstuk 3",
        course="Wiskunde",
        label="Huistaak",
        when=when,
    )
    with (
        patch(
            "smartschool_mcp.server.FutureTasks",
            side_effect=RuntimeError("agenda gone"),
        ),
        patch("smartschool_mcp.server.PlannedElements", return_value=[element]),
    ):
        result = srv.get_future_tasks()

    assert result["total_tasks"] == 1
    task = result["future_tasks"][0]["courses"][0]["tasks"][0]
    assert task["label"] == "Huistaak"
    assert task["description"] == "Hoofdstuk 3"


def test_get_messages_includes_attachment_fields() -> None:
    mock_header = MagicMock()
    mock_header.id = 1
    mock_header.from_ = "teacher@school.be"
    mock_header.subject = "Homework"
    mock_header.date = None
    mock_header.unread = False
    mock_header.priority = None
    # Simulate the library using 'attachments' as the field name
    mock_header.attachments = 2
    # Ensure getattr fallback for 'attachment' doesn't interfere
    del mock_header.attachment

    with patch("smartschool_mcp.server.MessageHeaders", return_value=[mock_header]):
        result = srv.get_messages(include_body=False)

    msg = result["messages"][0]
    assert msg["has_attachments"] is True
    assert msg["attachment_count"] == 2


def test_get_attachments_happy_path() -> None:
    mock_att = MagicMock(spec=Attachment)
    mock_att.file_id = 42
    mock_att.name = "homework.pdf"
    mock_att.mime = "application/pdf"
    mock_att.size = "100 KB"

    with patch("smartschool_mcp.server.Attachments", return_value=[mock_att]):
        result = srv.get_attachments(message_id=999)

    assert result["message_id"] == 999
    assert result["total"] == 1
    assert result["attachments"][0] == {
        "file_id": 42,
        "name": "homework.pdf",
        "mime_type": "application/pdf",
        "size": "100 KB",
    }


def test_get_attachments_returns_error_on_exception() -> None:
    with patch(
        "smartschool_mcp.server.Attachments", side_effect=RuntimeError("network")
    ):
        result = srv.get_attachments(message_id=999)
    assert "error" in result
    assert "network" in result["error"]


def test_download_attachment_not_found() -> None:
    with patch("smartschool_mcp.server.Attachments", return_value=[]):
        result = srv.download_attachment(message_id=999, file_id=42)
    assert "error" in result
    assert "not found" in result["error"]


def test_download_attachment_writes_file(tmp_path) -> None:
    mock_att = MagicMock(spec=Attachment)
    mock_att.file_id = 42
    mock_att.name = "report.pdf"
    mock_att.mime = "application/pdf"
    mock_att.size = "50 KB"

    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.content = b"PDF content"

    mock_session = MagicMock()
    mock_session.get.return_value = mock_resp

    with (
        patch("smartschool_mcp.server.Attachments", return_value=[mock_att]),
        patch("smartschool_mcp.server._session", return_value=mock_session),
    ):
        result = srv.download_attachment(
            message_id=999,
            file_id=42,
            save_path=str(tmp_path),
        )

    assert result["name"] == "report.pdf"
    assert result["bytes_written"] == len(b"PDF content")
    assert (tmp_path / "report.pdf").read_bytes() == b"PDF content"


def test_download_attachment_returns_error_on_exception() -> None:
    with patch("smartschool_mcp.server.Attachments", side_effect=RuntimeError("boom")):
        result = srv.download_attachment(message_id=999, file_id=42)
    assert "error" in result


# ── Homepage blocks ───────────────────────────────────────────────────────────

_HOMEPAGE_HTML = """
<div id="centercontainer">
  <div class="homepage__block" modname="news_in_de_kijker_84" newsid="84">
    <div class="homepage__block__top">
      <div class="homepage__block__top__title"><h2>Maandmenu</h2></div>
    </div>
    <div class="homepage__block__content">
      <p>Menu september</p>
      <p><img border="0" src="/public/school/Images/menu.PNG" width="891"></p>
      <a href="https://example.org/info">meer info</a>
    </div>
  </div>
  <div class="homepage__block" id="homepage__block--news">
    <div class="homepage__block__top">
      <div class="homepage__block__top__title"><h2>Nieuws</h2></div>
    </div>
    <div class="homepage__block__content"><div id="news_items"></div></div>
  </div>
</div>
"""


def _homepage_session() -> MagicMock:
    session = MagicMock()
    session.get.return_value = MagicMock(text=_HOMEPAGE_HTML)
    session.create_url.side_effect = lambda path: f"https://school.smartschool.be{path}"
    return session


def test_get_homepage_blocks_parses_title_image_and_link() -> None:
    with patch("smartschool_mcp.server._session", _homepage_session):
        result = srv.get_homepage_blocks()

    assert result["total"] == 2
    menu = result["blocks"][0]
    assert menu["title"] == "Maandmenu"
    assert menu["news_id"] == "84"
    assert menu["modname"] == "news_in_de_kijker_84"
    assert menu["text"] == "Menu september meer info"
    # site-relative image is made absolute; external link is left alone
    assert menu["images"] == [
        "https://school.smartschool.be/public/school/Images/menu.PNG"
    ]
    assert menu["links"] == ["https://example.org/info"]
    assert "html" not in menu


def test_get_homepage_blocks_include_html() -> None:
    with patch("smartschool_mcp.server._session", _homepage_session):
        result = srv.get_homepage_blocks(include_html=True)

    assert "<img" in result["blocks"][0]["html"]


def test_get_homepage_blocks_returns_error_on_exception() -> None:
    with patch("smartschool_mcp.server._session", side_effect=RuntimeError("network")):
        result = srv.get_homepage_blocks()
    assert "error" in result
    assert "network" in result["error"]


def test_download_homepage_image_writes_file(tmp_path) -> None:
    session = _homepage_session()
    session.get.return_value = MagicMock(ok=True, content=b"PNG bytes")

    with patch("smartschool_mcp.server._session", return_value=session):
        result = srv.download_homepage_image(
            "https://school.smartschool.be/public/school/Images/menu.PNG",
            str(tmp_path),
        )

    assert result["name"] == "menu.PNG"
    assert result["bytes_written"] == 9
    assert (tmp_path / "menu.PNG").read_bytes() == b"PNG bytes"


def test_download_homepage_image_rejects_foreign_host(tmp_path) -> None:
    with patch("smartschool_mcp.server._session", _homepage_session):
        result = srv.download_homepage_image(
            "https://evil.example.com/x.png", str(tmp_path)
        )

    assert "error" in result
    assert "Refusing" in result["error"]
    assert not list(tmp_path.iterdir())


# ── Download folder containment ───────────────────────────────────────────────


def test_download_dir_defaults_to_root(download_root) -> None:
    assert srv._download_dir(None) == download_root.resolve()
    assert srv._download_dir("  ") == download_root.resolve()


def test_download_dir_accepts_relative_subfolder(download_root) -> None:
    assert srv._download_dir("rapporten") == (download_root / "rapporten").resolve()


def test_download_dir_accepts_absolute_path_inside_root(download_root) -> None:
    inside = download_root / "a" / "b"
    assert srv._download_dir(str(inside)) == inside.resolve()


@pytest.mark.parametrize("bad", ["/tmp", "../escape", "sub/../../x", "~/.ssh"])
def test_download_dir_refuses_paths_outside_root(bad: str) -> None:
    with pytest.raises(srv.DownloadPathError, match="inside the download folder"):
        srv._download_dir(bad)


def test_download_dir_refuses_symlink_out_of_root(
    download_root, tmp_path_factory: pytest.TempPathFactory
) -> None:
    outside = tmp_path_factory.mktemp("outside")
    (download_root / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(srv.DownloadPathError):
        srv._download_dir("link")


def test_write_download_does_not_follow_dangling_symlink(
    download_root, tmp_path_factory: pytest.TempPathFactory
) -> None:
    target = tmp_path_factory.mktemp("outside") / "evil.pth"
    (download_root / "evil.pth").symlink_to(target)

    saved = srv._write_download(b"import os", "evil.pth", download_root)

    assert not target.exists()
    assert saved["name"] == "evil (1).pth"
    assert (download_root / "evil (1).pth").read_bytes() == b"import os"


def test_write_download_never_overwrites(download_root) -> None:
    (download_root / "menu.png").write_bytes(b"old")
    saved = srv._write_download(b"new", "menu.png", download_root)
    assert saved["name"] == "menu (1).png"
    assert (download_root / "menu.png").read_bytes() == b"old"


@pytest.mark.parametrize("raw", ["", ".", "..", "../../etc/.."])
def test_write_download_replaces_unusable_names(download_root, raw: str) -> None:
    saved = srv._write_download(b"x", raw, download_root)
    assert saved["name"] == "download"
    assert (download_root / "download").read_bytes() == b"x"


def test_write_download_strips_directories_from_name(download_root) -> None:
    saved = srv._write_download(
        b"x", "../../Library/LaunchAgents/a.plist", download_root
    )
    assert saved["saved_to"] == str(download_root / "a.plist")


def test_download_attachment_refuses_save_path_outside_root() -> None:
    with patch("smartschool_mcp.server.Attachments") as attachments:
        result = srv.download_attachment(999, 42, save_path="/tmp")
    assert "inside the download folder" in result["error"]
    attachments.assert_not_called()


def test_download_homepage_image_refuses_save_path_outside_root() -> None:
    session = _homepage_session()
    with patch("smartschool_mcp.server._session", return_value=session):
        result = srv.download_homepage_image(
            "https://school.smartschool.be/public/x.png", "~/Library/LaunchAgents"
        )
    assert "inside the download folder" in result["error"]
    session.get.assert_not_called()


def test_download_planner_file_refuses_save_path_outside_root() -> None:
    result = srv.download_planner_file(
        "0f0e0d0c-0b0a-4909-8807-060504030201", "file1", save_path="/etc"
    )
    assert "inside the download folder" in result["error"]


def test_download_course_document_refuses_save_path_outside_root() -> None:
    result = srv.download_course_document(1, 2, save_path="..")
    assert "inside the download folder" in result["error"]


# ── Input validation and portal pages ─────────────────────────────────────────


@pytest.mark.parametrize(("limit", "offset"), [(0, 0), (-1, 0), (201, 0), (5, -1)])
def test_paging_bounds_are_checked(limit: int, offset: int) -> None:
    with (
        patch("smartschool_mcp.server.Results") as results,
        patch("smartschool_mcp.server.MessageHeaders") as headers,
    ):
        assert "error" in srv.get_results(limit=limit, offset=offset)
        assert "error" in srv.get_messages(limit=limit, offset=offset)
    results.assert_not_called()
    headers.assert_not_called()


def test_get_results_survives_a_row_without_owner() -> None:
    row = MagicMock()
    row.courses = []
    row.gradebook_owner = None
    row.feedback = []
    with patch("smartschool_mcp.server.Results", return_value=[row]):
        result = srv.get_results(include_details=False)
    assert result["results"][0]["teacher"] is None


def test_message_body_search_is_capped() -> None:
    headers = [
        SimpleNamespace(id=i, subject="geen match", from_="x") for i in range(60)
    ]
    fetched: list[int] = []

    def _message(_session, msg_id):
        fetched.append(msg_id)
        return MagicMock(get=MagicMock(return_value=SimpleNamespace(body="")))

    with (
        patch("smartschool_mcp.server.MessageHeaders", return_value=headers),
        patch("smartschool_mcp.server.Message", side_effect=_message),
    ):
        result = srv.get_messages(search_query="toets")

    assert len(fetched) == srv._MAX_BODY_SEARCH
    assert result["body_search_truncated"] is True
    assert result["messages"] == []


@pytest.mark.parametrize(
    ("from_date", "to_date"),
    [("2026-05-10", "2026-05-01"), ("2026-01-01", "2028-01-01")],
)
def test_planner_window_rejects_bad_ranges(from_date: str, to_date: str) -> None:
    with patch("smartschool_mcp.server.fetch_calendar") as fetch:
        result = srv.get_planned_elements(from_date=from_date, to_date=to_date)
    assert "error" in result
    fetch.assert_not_called()


def _file_response(content: bytes, content_type: str, url: str = "") -> MagicMock:
    resp = MagicMock()
    resp.ok = True
    resp.content = content
    resp.url = url or "https://school.smartschool.be/file"
    resp.headers = {"Content-Type": content_type}
    return resp


def test_portal_page_detection() -> None:
    login = _file_response(
        b"<html>", "text/html", "https://school.smartschool.be/login"
    )
    assert srv._portal_page_instead_of_file(login, "application/pdf")
    html = _file_response(b"<!DOCTYPE html><html>", "application/octet-stream")
    assert srv._portal_page_instead_of_file(html, "application/pdf")
    pdf = _file_response(b"%PDF-1.7", "application/pdf")
    assert not srv._portal_page_instead_of_file(pdf, "application/pdf")
    attached_html = _file_response(b"<html>", "text/html")
    assert not srv._portal_page_instead_of_file(attached_html, "text/html")


def test_download_attachment_refuses_login_page(download_root) -> None:
    att = MagicMock(spec=Attachment)
    att.file_id = 42
    att.name = "report.pdf"
    att.mime = "application/pdf"
    session = MagicMock()
    session.get.return_value = _file_response(
        b"<html>login</html>", "text/html", "https://school.smartschool.be/login"
    )
    with (
        patch("smartschool_mcp.server.Attachments", return_value=[att]),
        patch("smartschool_mcp.server._session", return_value=session),
    ):
        result = srv.download_attachment(999, 42)
    assert "web page instead of the file" in result["error"]
    assert not list(download_root.iterdir())
