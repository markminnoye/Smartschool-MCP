"""Tests for MCP tool registration and error-handling behaviour."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from smartschool import Attachment

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
    with patch(
        "smartschool_mcp.server.FutureTasks", side_effect=RuntimeError("timeout")
    ):
        result = srv.get_future_tasks()
    assert isinstance(result, dict)
    assert "error" in result


def test_get_messages_returns_error_on_exception() -> None:
    with patch(
        "smartschool_mcp.server.MessageHeaders", side_effect=RuntimeError("403")
    ):
        result = srv.get_messages()
    assert isinstance(result, dict)
    assert "error" in result


def test_get_schedule_returns_error_on_exception() -> None:
    with patch(
        "smartschool_mcp.server.PlannedElements", side_effect=RuntimeError("503")
    ):
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


def test_get_planned_elements_returns_error_on_exception() -> None:
    with patch(
        "smartschool_mcp.server.PlannedElements", side_effect=RuntimeError("oops")
    ):
        result = srv.get_planned_elements()
    assert isinstance(result, dict)
    assert "error" in result


def _planner_element_mock() -> MagicMock:
    period = MagicMock()
    period.date_time_from.strftime.return_value = "2026-09-17 08:25"
    period.date_time_to.strftime.return_value = "2026-09-17 09:15"
    period.whole_day = False
    course = MagicMock()
    course.name = "Wiskunde"
    location = MagicMock()
    location.title = "A12"
    user = MagicMock()
    user.name.starting_with_first_name = "Jan Jansen"
    organisers = MagicMock()
    organisers.users = [user]
    element = MagicMock()
    element.name = "Wiskunde"
    element.planned_element_type = "planned-lessons"
    element.period = period
    element.color = "#ffcc00"
    element.courses = [course]
    element.locations = [location]
    element.organisers = organisers
    element.unconfirmed = False
    element.pinned = False
    element.assignment_type = None
    return element


def test_get_schedule_returns_planner_elements() -> None:
    element = _planner_element_mock()
    with patch(
        "smartschool_mcp.server.PlannedElements", return_value=[element]
    ) as mock_pe:
        result = srv.get_schedule(date_offset=0)

    assert result["total"] == 1
    assert "lessons" not in result
    item = result["elements"][0]
    assert item["type"] == "planned-lessons"
    assert item["name"] == "Wiskunde"
    assert item["courses"] == ["Wiskunde"]
    assert item["locations"] == ["A12"]
    assert item["organisers"] == ["Jan Jansen"]
    kwargs = mock_pe.call_args.kwargs
    assert kwargs.get("types") is None
    assert kwargs["from_date"] == kwargs["till_date"]


def test_get_planned_elements_omits_types_by_default() -> None:
    element = _planner_element_mock()
    with patch(
        "smartschool_mcp.server.PlannedElements", return_value=[element]
    ) as mock_pe:
        result = srv.get_planned_elements(days_ahead=7)

    assert result["total"] == 1
    assert result["planned_elements"][0]["type"] == "planned-lessons"
    kwargs = mock_pe.call_args.kwargs
    assert kwargs.get("types") is None
    assert kwargs.get("includes") is None


def test_get_planned_elements_passes_types_and_includes() -> None:
    with patch("smartschool_mcp.server.PlannedElements", return_value=[]) as mock_pe:
        srv.get_planned_elements(
            from_date="2026-09-17",
            to_date="2026-09-21",
            types="planned-assignments,planned-to-dos",
            includes="icon,courses",
        )

    kwargs = mock_pe.call_args.kwargs
    assert kwargs["types"] == "planned-assignments,planned-to-dos"
    assert kwargs["includes"] == "icon,courses"
    assert kwargs["from_date"].isoformat() == "2026-09-17"
    assert kwargs["till_date"].isoformat() == "2026-09-21"


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


def test_switch_child_does_not_library_get_foreign_verification(
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

    def fake_get(url: str, **kwargs: object) -> MagicMock:
        if "gotourl" in str(url):
            return gotourl
        if "otp" in str(url):
            return otp
        raise AssertionError(f"must not library-GET foreign auth {url} {kwargs}")

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
    assert not any("account-verification" in url for url in fetched)
    assert not any("login" in url for url in fetched)
    assert mock_session.creds.main_url == "school.smartschool.be"


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

    with patch("smartschool_mcp.server.FutureTasks", return_value=[mock_day]):
        result = srv.get_future_tasks()

    assert result["total_tasks"] == 2  # was broken before (returned 4 = len(task_dict))


def test_get_messages_invalid_box_type_defaults_to_inbox() -> None:
    with patch("smartschool_mcp.server.MessageHeaders", return_value=[]) as mock_mh:
        srv.get_messages(box_type="INVALID_BOX")
    # Should not raise; BoxType.INBOX used as fallback
    mock_mh.assert_called_once()


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
