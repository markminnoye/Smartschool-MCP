"""Mijn kinderen: listing linked children and switching between them.

A child on another school platform (school A -> school B) is reached through
a one-time ``/otp/...`` link. That school asks for the birth date of the child
being switched to; it is answered with a raw POST, never with the login's
password or TOTP, and a wrong answer must not write an ``auth_failed`` marker.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests

import smartschool_mcp.server as srv
from smartschool_mcp import children

HOME = "school-a.smartschool.be"
OTHER = "school-b.smartschool.be"
VERIFY_URL = f"https://{OTHER}/account-verification"
FORM = (
    '<form name="account_verification_form">'
    '<input type="date" name="account_verification_form[_security_question_answer]">'
    '<input type="hidden" name="account_verification_form[_token]" value="tok">'
    "</form>"
)


def _response(url: str, status: int = 200, location: str | None = None, body: str = ""):
    response = requests.Response()
    response.status_code = status
    response.url = url
    response._content = body.encode()
    if location:
        response.headers["Location"] = location
    return response


class FakeSession:
    def __init__(self) -> None:
        self.creds = SimpleNamespace(
            username="parent", main_url=HOME, password="secret-pw", mfa="2012-01-01"
        )
        self.posts: list[dict[str, Any]] = []
        self.password_logins = 0

    def create_url(self, path: str) -> str:
        return f"https://{self.creds.main_url}{path}"

    def get(self, url: str, **_kwargs: Any):
        return _response(
            f"https://{HOME}{url}",
            302,
            f"https://{OTHER}/otp/token",
        )

    def _do_login(self, *_args: Any, **_kwargs: Any) -> None:
        self.password_logins += 1


@pytest.fixture
def session(monkeypatch: pytest.MonkeyPatch) -> FakeSession:
    fake = FakeSession()
    hops = {
        f"https://{OTHER}/otp/token": _response(
            f"https://{OTHER}/otp/token", 302, "/Studentcard"
        ),
        f"https://{OTHER}/Studentcard": _response(
            f"https://{OTHER}/Studentcard", 302, "/account-verification"
        ),
        VERIFY_URL: _response(VERIFY_URL, body=FORM),
    }
    monkeypatch.setattr(
        children, "_foreign_hop_get", lambda _s, url, _origin: hops[url]
    )
    return fake


def _post_answering(fake: FakeSession, monkeypatch, accepted: str | None):
    def raw(_session, method, url, **kwargs):
        fake.posts.append({"method": method, "url": url, "data": kwargs["data"]})
        answer = kwargs["data"]["account_verification_form[_security_question_answer]"]
        landing = f"https://{OTHER}/Studentcard" if answer == accepted else VERIFY_URL
        return _response(landing)

    monkeypatch.setattr(children, "_raw_session_request", raw)


def test_answers_with_the_childs_birth_date(session, monkeypatch, tmp_path):
    monkeypatch.setattr(
        children, "auth_failed_path", lambda *_: tmp_path / "auth_failed"
    )
    _post_answering(session, monkeypatch, accepted="2009-05-17")

    response, host, blocked = children._follow_child_switch_redirects(
        session, "/Studentcard/Chain/gotourl/accountID/1", "2009-05-17"
    )

    assert blocked is None
    assert host == OTHER
    assert str(response.url).endswith("/Studentcard")
    [post] = session.posts
    assert post["data"]["account_verification_form[_security_question_answer]"] == (
        "2009-05-17"
    )
    assert "secret-pw" not in str(post)
    assert session.password_logins == 0


def test_without_birth_date_nothing_is_posted(session, monkeypatch):
    _post_answering(session, monkeypatch, accepted=None)

    _resp, _host, blocked = children._follow_child_switch_redirects(
        session, "/Studentcard/Chain/gotourl/accountID/1"
    )

    assert blocked == VERIFY_URL
    assert session.posts == []


def test_wrong_birth_date_posts_once_and_writes_no_lock(session, monkeypatch, tmp_path):
    lock = tmp_path / "auth_failed"
    monkeypatch.setattr(children, "auth_failed_path", lambda *_: lock)
    _post_answering(session, monkeypatch, accepted="2009-05-17")

    _r, _host, blocked = children._follow_child_switch_redirects(
        session, "/Studentcard/Chain/gotourl/accountID/1", "2010-01-01"
    )

    assert blocked == VERIFY_URL
    assert len(session.posts) == 1
    assert not lock.exists()


def test_success_removes_a_stale_lock(session, monkeypatch, tmp_path):
    lock = tmp_path / "auth_failed"
    lock.write_text("login failed\n")
    monkeypatch.setattr(children, "auth_failed_path", lambda *_: lock)
    _post_answering(session, monkeypatch, accepted="2009-05-17")

    children._follow_child_switch_redirects(
        session, "/Studentcard/Chain/gotourl/accountID/1", "2009-05-17"
    )

    assert not lock.exists()


# -- get_children / switch_child tools ----------------------------------------------


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
            "firstName": "Anna",
            "lastName": "Peeters",
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
        "firstName": "Anna",
        "lastName": "Peeters",
        "username": "anna.peeters",
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
    assert result["children"][0]["name"] == "Anna Peeters"
    assert result["children"][0]["class_name"] == "1bb"
    assert result["current"]["name"] == "Anna Peeters"


def test_get_children_uses_topnav_id_when_account_id_is_zero(
    mock_session: MagicMock,
) -> None:
    mock_session.json.return_value = [
        {
            "accountID": 0,
            "userID": 11,
            "name": "Anna",
            "surname": "Peeters",
            "fullNameBIN": "Anna Peeters",
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
            "<img src='/p' alt='' /><span>Anna</span></a>"
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
    assert elliot["name"] == "Anna Peeters"
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
            '<a href="/Studentcard/Chain/gotourl/accountID/333"><span>Anna</span></a>'
        ),
        url="/Studentcard",
    )
    mock_session.authenticated_user = {"id": "parent"}

    result = srv.get_children()

    assert result["total"] == 1
    assert result["children"][0]["account_id"] == "333"
    assert result["children"][0]["first_name"] == "Anna"


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
      "id": "33_2", "firstName": "Lars", "lastName": "Child"
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
    assert result["current"]["name"] == "Lars Child"
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
    assert not any(children._url_is_login(url) for url in fetched)
    assert not any(
        call.kwargs.get("allow_redirects") is True
        for call in mock_session.get.call_args_list
    )


def test_switch_child_asks_for_birth_date_on_foreign_verification(
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
    assert "birth date" in result["error"]
    fetched = [str(call.args[0]) for call in mock_session.get.call_args_list]
    # The other school's page never goes through the guarded session, so
    # nothing is posted there and no lock can be written.
    assert not any("account-verification" in url for url in fetched)
    assert not any(children._url_is_login(url) for url in fetched)
    assert mock_session.creds.main_url == "school.smartschool.be"


# -- birth dates stored per child -----------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("17-05-2009", "2009-05-17"),
        ("17/05/2009", "2009-05-17"),
        ("2009-05-17", "2009-05-17"),
    ],
)
def test_child_birth_date_accepts_day_first(raw: str, expected: str) -> None:
    assert children.child_birth_date(raw) == expected


@pytest.mark.parametrize("raw", ["morgen", "31-02-2009", "JBSWY3DPEHPK3PXP"])
def test_child_birth_date_refuses_anything_else(raw: str) -> None:
    assert children.child_birth_date(raw) is None


def test_switch_child_refuses_a_bad_birth_date_before_any_request(
    mock_session: MagicMock,
) -> None:
    result = srv.switch_child("5001", "morgen")

    assert "dd-mm-yyyy" in result["error"]
    mock_session.get.assert_not_called()


def test_accepted_birth_date_is_stored_once_and_reused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    fake = FakeSession()
    fake.ensure_authenticated = lambda: None  # type: ignore[attr-defined]
    seen: list[str | None] = []

    def chain(session, _path, birth):
        seen.append(birth)
        session.__dict__["_verified_birth"] = birth
        return _response(f"https://{OTHER}/Studentcard"), OTHER, None

    monkeypatch.setattr(children, "_follow_child_switch_redirects", chain)
    monkeypatch.setattr(children, "_child_name_for", lambda *_: "Kim")
    monkeypatch.setattr(
        children, "_authenticated_user_from_response", lambda *_: {"name": "x"}
    )

    first = children.switch(fake, "5001", "14/06/2010")
    second = children.switch(fake, "5001")

    assert first["ok"] is True
    assert first["saved_birth_date"] is True
    assert "saved_birth_date" not in second
    assert seen == ["2010-06-14", "2010-06-14"]
    stored = tmp_path / ".cache" / "smartschool" / HOME / "parent" / "children.json"
    assert json.loads(stored.read_text()) == {
        "5001": {"name": "Kim", "birth_date": "2010-06-14"}
    }
    assert stored.stat().st_mode & 0o777 == 0o600
