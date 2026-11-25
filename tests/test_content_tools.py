"""Tests for the planner detail, course document and leerpad tools.

They replay anonymised Smartschool responses from ``tests/fixtures``.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from smartschool import FileItem, FolderItem, InternetShortcut, PlannedElement

import smartschool_mcp.server as srv
from smartschool_mcp import content

FIXTURES = Path(__file__).parent / "fixtures"
BASE = "https://school.example/"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf8")


def _raw_elements() -> list[dict[str, Any]]:
    return json.loads(_fixture("planner_elements.json"))


def _elements() -> list[PlannedElement]:
    return [PlannedElement(**e) for e in _raw_elements()]


@pytest.fixture(autouse=True)
def _clean_index() -> None:
    srv._planner_index.clear()


@pytest.fixture
def session(mock_session: MagicMock) -> MagicMock:
    mock_session.create_url.side_effect = lambda url: BASE.rstrip("/") + url
    return mock_session


def _page(url: str, text: str = "", **kwargs: Any) -> SimpleNamespace:
    return SimpleNamespace(url=BASE.rstrip("/") + url, text=text, **kwargs)


# ── content parsers ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("name", "assignment_type", "expected"),
    [
        ("Examen geschiedenis", None, True),
        ("Examens", None, True),
        ("Toets statistiek", None, True),
        ("Test 4: Irregular verbs", None, True),
        ("Herhaling", "Digitale toets", True),
        ("Toetsvrije week", None, False),
        ("taak optica", "Huistaak", False),
        ("", None, False),
    ],
)
def test_is_exam(name: str, assignment_type: str | None, expected: bool) -> None:
    assert content.is_exam(name, assignment_type) is expected


def test_html_to_text_keeps_paragraphs_and_list_items() -> None:
    text = content.html_to_text(
        "<p>Intro<br />tweede lijn</p>"
        "<ul><li>een</li><li><strong>- twee</strong></li></ul>"
    )
    assert text == "Intro\ntweede lijn\n- een\n- twee"
    assert content.html_to_text(None) == ""


def test_planner_detail_lists_attachments_and_every_link() -> None:
    raw = json.loads(_fixture("planner_detail.json"))
    detail = content.planner_detail(raw, BASE)

    assert detail["type_label"] == "Lesson"
    assert detail["courses"] == ["Artistieke expressie"]
    assert detail["attachments"] == [
        {
            "id": raw["attachments"][0]["id"],
            "name": "Surrealistische Magritte-kameropdracht.png",
            "size": 2084793,
            "mime_type": "image/png",
        }
    ]
    kinds = [(link["kind"], link["label"]) for link in detail["links"]]
    assert kinds == [
        ("description", "Extra oefening"),
        ("weblink", "Uitleg op video"),
        ("document", "Artistieke Expressie Bundel 3e jaars"),
        ("document", "Artistieke Expressie.pptx"),
    ]
    folder, file = detail["links"][2:]
    assert folder["document"] == {
        "course_id": 8608,
        "doc_id": 359984,
        "is_folder": True,
    }
    assert file["document"]["is_folder"] is False
    assert file["url"].startswith("https://school.example/deeplink/")
    # Bookwidget titles Smartschool sends without an address.
    assert detail["links_without_url"] == [
        "Besturingssystemen, bits & bytes en netwerken",
        "Een computer kopen",
        "snel en efficiënt zoeken & backup",
    ]
    assert "Opdracht 2: Verwerk de theorie" in detail["description"]


def test_parse_lpath_folder_lists_folders_and_learning_paths() -> None:
    assert content.parse_lpath_folder(_fixture("lpaths_root.html")) == [
        {"kind": "folder", "id": 4279, "name": "Leerpaden"}
    ]
    paths = content.parse_lpath_folder(_fixture("lpaths_folder.html"))
    assert [(p["kind"], p["id"], p["folder_id"]) for p in paths] == [
        ("learning_path", 4285, 4279),
        ("learning_path", 4283, 4279),
        ("learning_path", 5732, 4279),
    ]
    assert paths[2]["name"] == "Leerpad - Hardware VS Software"


def test_parse_lpath_view_reads_sections_steps_and_progress() -> None:
    view = content.parse_lpath_view(_fixture("lpaths_view.html"))

    assert view["name"] == "Leerpad - Hardware VS Software"
    assert isinstance(view["progress_percent"], int)
    titles = [s["title"] for s in view["sections"]]
    assert titles[0] == "Wat moet je in dit leerpad doen?"
    steps = [step for s in view["sections"] for step in s["steps"]]
    assert {s["module"] for s in steps} == {"documents", "weblinks", "exercises"}
    first = steps[0]
    assert first == {
        "module": "documents",
        "item_id": 309790,
        "name": "Leerpad - Hardware vs Software ovur.pdf",
        "done": True,
    }
    assert any(not s["done"] for s in steps)


def test_parse_exercise_folder_finds_bookwidgets() -> None:
    folders, exercises = content.parse_exercise_folder(
        _fixture("exercises_folder.html")
    )

    assert folders == []
    assert exercises[44230]["provider"] == "Bookwidgets"
    assert exercises[44230]["name"] == "Een computer kopen"
    assert "openExternalExercise&exerciseID=44230" in exercises[44230]["url"]


# ── get_planned_elements ─────────────────────────────────────────────────────


def _list_call(session: MagicMock) -> tuple[str, dict[str, Any]]:
    """The planner list request get_planned_elements made."""
    url = session.json.call_args.args[0]
    return url, session.json.call_args.kwargs["data"]


def test_get_planned_elements_default_skips_timetable_but_keeps_exams(
    session: MagicMock,
) -> None:
    session.json.return_value = _raw_elements()
    result = srv.get_planned_elements()

    url, params = _list_call(session)
    assert url.startswith("/planner/api/v1/planned-elements/user/")
    assert "types" not in params
    names = [(e["type"], e["name"]) for e in result["planned_elements"]]
    types = {t for t, _ in names}
    assert "planned-placeholders" not in types
    assert ("planned-lessons", "Examen") in names
    assert ("planned-meetings", "Examens") in names
    assert ("planned-lesson-free-days", "Kerstvakantie") in names
    # A plain lesson is timetable, not something to plan for.
    assert not any(n.startswith("Hoofdstuk") for _, n in names)
    # The lesson twin of a test is dropped, the assignment kept.
    toets = [t for t, n in names if n.startswith("Toets zuivere stoffen")]
    assert toets == ["planned-assignments"]
    exam = next(e for e in result["planned_elements"] if e["name"] == "Examen")
    assert exam["is_exam"] is True
    assert exam["type_label"] == "Lesson"
    assert exam["id"] and exam["platform_id"] == 49
    assert result["planned_elements"] == sorted(
        result["planned_elements"], key=lambda e: e["from"]
    )
    # Ids are remembered for the detail tools.
    assert exam["id"] in srv._planner_index


def test_get_planned_elements_asks_the_content_in_the_same_request(
    session: MagicMock,
) -> None:
    session.json.return_value = _raw_elements()
    srv.get_planned_elements()

    session.json.assert_called_once()
    includes = _list_call(session)[1]["includes"].split(",")
    # The defaults first: asking for any include drops them.
    assert includes[:3] == ["icon", "courses", "locations"]
    assert {"attachments", "weblinks", "deeplinks", "public-info"} <= set(includes)


def test_get_planned_elements_flags_what_an_element_has_to_show(
    session: MagicMock,
) -> None:
    session.json.return_value = _raw_elements()
    result = srv.get_planned_elements()

    flags = {
        e["name"]: (e["has_attachments"], e["has_links"], e["has_description"])
        for e in result["planned_elements"]
    }
    toets = next(n for n in flags if n.startswith("Toets zuivere stoffen"))
    assert flags[toets] == (True, False, True)
    # A description that only repeats the name is no description.
    assert flags["taak optica"] == (False, False, False)
    # A Bookwidget title without an address counts as a link.
    assert flags["Examen"] == (False, True, True)
    assert flags["Examens"] == (False, True, False)
    assert flags["Kerstvakantie"] == (False, False, False)
    assert "details" not in result["planned_elements"][0]


def test_get_planned_elements_types_returns_exactly_what_was_asked(
    session: MagicMock,
) -> None:
    lessons = [
        e for e in _raw_elements() if e["plannedElementType"] == "planned-lessons"
    ]
    session.json.return_value = lessons
    result = srv.get_planned_elements(types=["planned-lessons"])

    assert _list_call(session)[1]["types"] == "planned-lessons"
    assert result["total"] == len(lessons)


def test_get_planned_elements_include_details_needs_no_extra_request(
    session: MagicMock,
) -> None:
    session.json.return_value = _raw_elements()
    result = srv.get_planned_elements(include_details=True)

    session.json.assert_called_once()
    exam = next(e for e in result["planned_elements"] if e["name"] == "Examen")
    assert set(exam["details"]) == set(srv._DETAIL_KEYS)
    assert exam["details"]["description"].startswith("Leerstof: thema 1 tot 3.")
    assert exam["details"]["links_without_url"] == ["Oefentoets"]


def test_get_planned_elements_empty_answer(session: MagicMock) -> None:
    session.json.return_value = {}
    result = srv.get_planned_elements()

    assert result["total"] == 0


# ── planner element details and attachments ──────────────────────────────────


def test_get_planned_element_details_finds_the_element_type(session: MagicMock) -> None:
    session.json.return_value = json.loads(_fixture("planner_detail.json"))
    element = _elements()[0]
    with patch("smartschool_mcp.server.PlannedElements", return_value=[element]) as pe:
        result = srv.get_planned_element_details(str(element.id))

    pe.assert_called_once()
    session.json.assert_called_once_with(
        f"/planner/api/v1/{element.planned_element_type}/49/{element.id}"
    )
    assert result["name"] == "Leerkracht afwezig: zie opdracht"


def test_get_planned_element_details_uses_known_ids(session: MagicMock) -> None:
    session.json.return_value = json.loads(_fixture("planner_detail.json"))
    srv._planner_index["abc"] = ("planned-lessons", 49)
    with patch("smartschool_mcp.server.PlannedElements") as pe:
        srv.get_planned_element_details("abc")

    pe.assert_not_called()
    session.json.assert_called_once_with("/planner/api/v1/planned-lessons/49/abc")


def test_get_planned_element_details_unknown_id() -> None:
    with patch("smartschool_mcp.server.PlannedElements", return_value=[]):
        result = srv.get_planned_element_details("missing")

    assert "not found" in result["error"]


def test_download_planned_attachment(session: MagicMock, tmp_path: Path) -> None:
    detail = json.loads(_fixture("planner_detail.json"))
    session.json.return_value = detail
    session.get.return_value = SimpleNamespace(ok=True, content=b"png-bytes")
    srv._planner_index[detail["id"]] = ("planned-lessons", 49)
    attachment_id = detail["attachments"][0]["id"]

    first = srv.download_planned_attachment(
        detail["id"], attachment_id, save_path=str(tmp_path)
    )
    second = srv.download_planned_attachment(
        detail["id"], attachment_id, save_path=str(tmp_path)
    )

    session.get.assert_called_with(
        f"/planner/api/v1/planned-lessons/49/{detail['id']}"
        f"/attachments/{attachment_id}/download"
    )
    assert first["bytes_written"] == len(b"png-bytes")
    assert Path(first["saved_to"]).read_bytes() == b"png-bytes"
    assert second["name"] == "Surrealistische Magritte-kameropdracht (1).png"


def test_download_planned_attachment_unknown_attachment(session: MagicMock) -> None:
    session.json.return_value = json.loads(_fixture("planner_detail.json"))
    srv._planner_index["abc"] = ("planned-lessons", 49)

    result = srv.download_planned_attachment("abc", "nope")

    assert "not found" in result["error"]
    session.get.assert_not_called()


# ── course documents ─────────────────────────────────────────────────────────

COURSES = [
    SimpleNamespace(id=10, platform_id=49, name="Wiskunde 3", teacher="X"),
    SimpleNamespace(id=11, platform_id=49, name="Wiskunde 4", teacher="Y"),
    SimpleNamespace(id=12, platform_id=49, name="ICT-vaardigheden", teacher="Z"),
]


def _file(doc_id: int, name: str) -> MagicMock:
    item = MagicMock(spec=FileItem)
    item.id, item.name, item.filename = doc_id, name, name
    item.mime_type, item.size_kb, item.last_modified = "pdf", 12.5, "2026-11-01"
    item.download.return_value = b"%PDF"
    return item


def _folder(folder_id: int, name: str, items: list[Any]) -> MagicMock:
    folder = MagicMock(spec=FolderItem)
    folder.name = name
    folder.browse_url = (
        f"/Documents/Index/Index/courseID/12/parentID/{folder_id}/ssID/49"
    )
    folder.items = items
    return folder


def test_get_course_documents_without_course_lists_courses() -> None:
    with patch("smartschool_mcp.server.TopNavCourses", return_value=COURSES):
        result = srv.get_course_documents()

    assert [c["id"] for c in result["courses"]] == [10, 11, 12]


def test_get_course_documents_lists_a_folder() -> None:
    link = MagicMock(spec=InternetShortcut)
    link.name, link.link = "Pelckmans Portaal", "https://portaal.example"
    root = _folder(
        0, "(Root)", [_folder(7, "Examenbundel", []), _file(5, "a.pdf"), link]
    )
    with (
        patch("smartschool_mcp.server.TopNavCourses", return_value=COURSES),
        patch("smartschool_mcp.server._document_folder", return_value=root) as folder,
    ):
        result = srv.get_course_documents("ict")

    assert folder.call_args.args[2] is None
    assert result["course"] == {"id": 12, "name": "ICT-vaardigheden"}
    assert result["entries"] == [
        {"kind": "folder", "folder_id": 7, "name": "Examenbundel"},
        {
            "kind": "file",
            "doc_id": 5,
            "name": "a.pdf",
            "mime_type": "pdf",
            "size_kb": 12.5,
            "modified": "2026-11-01",
        },
        {"kind": "link", "name": "Pelckmans Portaal", "url": "https://portaal.example"},
    ]


def test_document_folder_builds_the_folder_url(session: MagicMock) -> None:
    folder = srv._document_folder(session, COURSES[2], 7)
    assert folder.browse_url == "/Documents/Index/Index/courseID/12/parentID/7/ssID/49"
    assert srv._document_folder(session, COURSES[2], None).browse_url == (
        "/Documents/Index/Index/courseID/12/ssID/49"
    )


def test_ambiguous_course_names_the_candidates() -> None:
    with patch("smartschool_mcp.server.TopNavCourses", return_value=COURSES):
        result = srv.get_course_documents("wiskunde")

    assert "Several courses" in result["error"]
    assert "Wiskunde 3 (id 10)" in result["error"]


def test_course_by_id() -> None:
    with patch("smartschool_mcp.server.TopNavCourses", return_value=COURSES):
        assert srv._find_course(MagicMock(), "11") is COURSES[1]


def test_download_course_document_searches_sub_folders(tmp_path: Path) -> None:
    root = _folder(0, "(Root)", [_folder(7, "Examenbundel", [_file(5, "bundel.pdf")])])
    with (
        patch("smartschool_mcp.server.TopNavCourses", return_value=COURSES),
        patch("smartschool_mcp.server._document_folder", return_value=root),
    ):
        result = srv.download_course_document("12", 5, save_path=str(tmp_path))

    assert result["name"] == "bundel.pdf"
    assert (tmp_path / "bundel.pdf").read_bytes() == b"%PDF"


def test_download_course_document_not_found() -> None:
    with (
        patch("smartschool_mcp.server.TopNavCourses", return_value=COURSES),
        patch(
            "smartschool_mcp.server._document_folder", return_value=_folder(0, "", [])
        ),
    ):
        result = srv.download_course_document("12", 5)

    assert "not found" in result["error"]


# ── leerpaden ────────────────────────────────────────────────────────────────


def test_get_learning_paths_walks_the_folders(session: MagicMock) -> None:
    pages = {
        "/Lpaths/Index/Index/courseID/12/ssID/49": _fixture("lpaths_root.html"),
        "/Lpaths/Index/Index/courseID/12/parentID/4279": _fixture("lpaths_folder.html"),
    }
    session.get.side_effect = lambda url, **_: _page(url, pages[url])
    with patch("smartschool_mcp.server.TopNavCourses", return_value=COURSES):
        result = srv.get_learning_paths("ICT")

    assert result["total"] == 3
    assert result["learning_paths"][2]["id"] == 5732
    assert result["learning_paths"][2]["folder"] == "Leerpaden"


def test_get_learning_paths_course_without_module(session: MagicMock) -> None:
    session.get.side_effect = lambda url, **_: _page("/")
    with patch("smartschool_mcp.server.TopNavCourses", return_value=COURSES):
        result = srv.get_learning_paths("ICT")

    assert result["learning_paths"] == []
    assert result["note"] == "No leerpaden module"


def test_get_learning_path_resolves_web_links_and_exercises(session: MagicMock) -> None:
    def get(url: str, **kwargs: Any) -> SimpleNamespace:
        if url.startswith("/Lpaths/Viewlpath/"):
            return _page(url, _fixture("lpaths_view.html"))
        if url.startswith("/?module=Exercises"):
            return _page(url, _fixture("exercises_folder.html"))
        assert url.startswith("/Weblinks/Showlink/") and not kwargs["allow_redirects"]
        link_id = url.split("/linkID/")[1].split("/")[0]
        return SimpleNamespace(
            is_redirect=True, headers={"Location": f"https://video.example/{link_id}"}
        )

    session.get.side_effect = get
    with patch("smartschool_mcp.server.TopNavCourses", return_value=COURSES):
        result = srv.get_learning_path("ICT", 5732)

    steps = [step for s in result["sections"] for step in s["steps"]]
    weblink = next(s for s in steps if s["module"] == "weblinks")
    assert weblink["url"] == f"https://video.example/{weblink['item_id']}"
    computer = next(s for s in steps if s["name"] == "Een computer kopen")
    assert computer["provider"] == "Bookwidgets"
    assert computer["url"].startswith("https://school.example/?module=Exercises")
    assert result["total_steps"] == len(steps)
    # Steps are never opened: only the overview, exercises and link redirects.
    opened = [c.args[0] for c in session.get.call_args_list]
    assert not any("/modules/" in url for url in opened)


def test_get_learning_path_without_resolving(session: MagicMock) -> None:
    session.get.side_effect = lambda url, **_: _page(url, _fixture("lpaths_view.html"))
    with patch("smartschool_mcp.server.TopNavCourses", return_value=COURSES):
        result = srv.get_learning_path("ICT", 5732, resolve_links=False)

    assert session.get.call_count == 1
    assert all("url" not in s for sec in result["sections"] for s in sec["steps"])


# ── every new tool reports failures as a result ──────────────────────────────


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        (srv.get_planned_elements, ()),
        (srv.get_planned_element_details, ("x", "planned-lessons")),
        (srv.download_planned_attachment, ("x", "y", "planned-lessons")),
        (srv.get_course_documents, ("ict",)),
        (srv.download_course_document, ("ict", 1)),
        (srv.get_learning_paths, ("ict",)),
        (srv.get_learning_path, ("ict", 1)),
    ],
)
def test_new_tools_return_errors(tool: Any, args: tuple[Any, ...]) -> None:
    with (
        patch("smartschool_mcp.server._session", side_effect=RuntimeError("down")),
    ):
        result = tool(*args)

    assert "down" in result["error"]


def test_planner_summary_dates() -> None:
    element = _elements()[0]
    summary = srv._planner_element_summary(element)
    assert datetime.strptime(summary["from"], "%Y-%m-%d %H:%M")


@pytest.mark.parametrize(
    ("lesson_name", "lesson_courses", "lesson_day", "expected"),
    [
        ("Revision test", ["Engels"], "2026-11-25", True),
        ("revision test (see info)", ["Engels"], "2026-11-25", True),
        ("Revision test", ["Chemie"], "2026-11-25", False),
        ("Revision test", ["Engels"], "2026-11-26", False),
        ("Examen", ["Engels"], "2026-11-25", False),
    ],
)
def test_same_test(
    lesson_name: str, lesson_courses: list[str], lesson_day: str, expected: bool
) -> None:
    assignment = {
        "type": "planned-assignments",
        "name": "Revision test (see info)",
        "courses": ["Engels"],
        "from": "2026-11-25 08:25",
    }
    lesson = {
        "type": "planned-lessons",
        "name": lesson_name,
        "courses": lesson_courses,
        "from": f"{lesson_day} 08:25",
    }
    assert srv._same_test(lesson, assignment) is expected
    assert srv._same_test(assignment, lesson) is False
