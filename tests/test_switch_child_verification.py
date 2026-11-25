"""Switching to a child on another school platform (De Pass -> De Ring).

The other school asks for the birth date of the child being switched to. It is
answered with a raw POST: never with the login's password, TOTP or ``mfa``, and
a wrong answer must not write an ``auth_failed`` lock.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
import requests

from smartschool_mcp import credentials, server

HOME = "depass.smartschool.be"
OTHER = "dering.smartschool.be"
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
        server, "_foreign_hop_get", lambda _s, url, _origin: hops[url]
    )
    return fake


def _post_answering(fake: FakeSession, monkeypatch, accepted: str | None):
    def raw(_session, method, url, **kwargs):
        fake.posts.append({"method": method, "url": url, "data": kwargs["data"]})
        answer = kwargs["data"]["account_verification_form[_security_question_answer]"]
        landing = f"https://{OTHER}/Studentcard" if answer == accepted else VERIFY_URL
        return _response(landing)

    monkeypatch.setattr(server, "_raw_session_request", raw)


def test_answers_with_the_childs_birth_date(session, monkeypatch, tmp_path):
    monkeypatch.setattr(server, "auth_failed_path", lambda *_: tmp_path / "auth_failed")
    _post_answering(session, monkeypatch, accepted="2009-05-17")

    response, host, blocked = server._follow_child_switch_redirects(
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

    _response_, _host, blocked = server._follow_child_switch_redirects(
        session, "/Studentcard/Chain/gotourl/accountID/1"
    )

    assert blocked == VERIFY_URL
    assert session.posts == []


def test_wrong_birth_date_posts_once_and_writes_no_lock(
    session, monkeypatch, tmp_path
):
    lock = tmp_path / "auth_failed"
    monkeypatch.setattr(server, "auth_failed_path", lambda *_: lock)
    _post_answering(session, monkeypatch, accepted="2009-05-17")

    _r, _host, blocked = server._follow_child_switch_redirects(
        session, "/Studentcard/Chain/gotourl/accountID/1", "2010-01-01"
    )

    assert blocked == VERIFY_URL
    assert len(session.posts) == 1
    assert not lock.exists()


def test_success_removes_a_stale_lock(session, monkeypatch, tmp_path):
    lock = tmp_path / "auth_failed"
    lock.write_text("login failed\n")
    monkeypatch.setattr(server, "auth_failed_path", lambda *_: lock)
    _post_answering(session, monkeypatch, accepted="2009-05-17")

    server._follow_child_switch_redirects(
        session, "/Studentcard/Chain/gotourl/accountID/1", "2009-05-17"
    )

    assert not lock.exists()


def test_child_record_keeps_a_normalized_birth_date() -> None:
    [child] = credentials.child_records(
        {"children": [{"name": "Kim", "account_id": "5001", "birth_date": "17.05.2009"}]}
    )
    assert child["birth_date"] == "2009-05-17"
    assert child["account_id"] == "5001"


def test_child_record_drops_an_invalid_birth_date() -> None:
    [child] = credentials.child_records(
        {"children": [{"name": "Kim", "birth_date": "morgen"}]}
    )
    assert "birth_date" not in child


def _store(monkeypatch, children):
    profiles = [
        {
            "username": "parent",
            "main_url": HOME,
            "password": "x",
            "mfa": "2012-01-01",
            "children": children,
        }
    ]
    written: list[Any] = []
    monkeypatch.setattr(credentials, "load_profiles", lambda: profiles)
    monkeypatch.setattr(credentials, "_write_profiles", lambda p: written.append(p))
    return profiles, written


def test_save_child_birth_date_adds_a_new_child(monkeypatch) -> None:
    profiles, written = _store(
        monkeypatch, [{"name": "Alex", "account_id": "5002"}]
    )

    assert credentials.save_child_birth_date("parent", "5001", "Kim", "14/06/2010")

    assert written == [profiles]
    assert profiles[0]["children"] == [
        {"name": "Alex", "account_id": "5002"},
        {"name": "Kim", "account_id": "5001", "birth_date": "2010-06-14"},
    ]


def test_save_child_birth_date_updates_the_same_account(monkeypatch) -> None:
    profiles, _ = _store(
        monkeypatch,
        [{"name": "Kim", "account_id": "5001", "birth_date": "2010-01-01"}],
    )

    credentials.save_child_birth_date("parent", "5001", "Kim", "2010-06-14")

    assert profiles[0]["children"][0]["birth_date"] == "2010-06-14"
    assert len(profiles[0]["children"]) == 1


def test_save_child_birth_date_needs_exactly_one_login(monkeypatch) -> None:
    _, written = _store(monkeypatch, [])

    assert not credentials.save_child_birth_date("someone-else", "1", "X", "2010-06-14")
    assert written == []


def test_switch_child_saves_a_new_birth_date_once_accepted(monkeypatch) -> None:
    fake = FakeSession()
    fake.ensure_authenticated = lambda: None  # type: ignore[attr-defined]
    saved: list[tuple[Any, ...]] = []

    def chain(session, _path, birth):
        session.__dict__["_verified_birth"] = birth
        return _response(f"https://{OTHER}/Studentcard"), OTHER, None

    monkeypatch.setattr(server, "_session", lambda: fake)
    monkeypatch.setattr(server, "_follow_child_switch_redirects", chain)
    monkeypatch.setattr(server, "_stored_child_birth_date", lambda *_: None)
    monkeypatch.setattr(server, "_child_name_for", lambda _id: "Kim")
    monkeypatch.setattr(
        server, "_authenticated_user_from_response", lambda *_: {"name": "x"}
    )
    monkeypatch.setattr(server, "_person_dict", lambda user: user)
    monkeypatch.setattr(
        server, "save_child_birth_date", lambda *args: saved.append(args) or True
    )

    result = server.switch_child("5001", "14/06/2010")

    assert result["ok"] is True
    assert result["saved_birth_date"] is True
    assert saved == [("parent", "5001", "Kim", "2010-06-14")]
