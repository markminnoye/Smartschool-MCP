"""Unit tests for the Claude Code plugin scripts. No network."""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from smartschool import Smartschool, SmartSchoolAuthenticationError

import _common
import courses
import login
import messages
import results
import schedule
from _common import (
    hold_session_lock,
    load_config,
    open_session,
    parse_env_file,
    planned_element,
)

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "claude-plugin"


def test_example_env_lists_one_account_and_no_secrets() -> None:
    parsed = parse_env_file((PLUGIN / "config.example.env").read_text(encoding="utf-8"))
    assert set(parsed) == {
        "SMARTSCHOOL_MAIN_URL",
        "SMARTSCHOOL_USERNAME",
        "SMARTSCHOOL_PASSWORD",
        "SMARTSCHOOL_MFA",
    }
    assert parsed["SMARTSCHOOL_PASSWORD"] == ""
    assert parsed["SMARTSCHOOL_MFA"] == ""
    assert parsed["SMARTSCHOOL_USERNAME"] == ""
    assert "https://" not in parsed["SMARTSCHOOL_MAIN_URL"]


def test_parse_env_file_skips_comments_and_strips_quotes() -> None:
    parsed = parse_env_file(
        '\n# comment\nexport SMARTSCHOOL_USERNAME="student"\n'
        "SMARTSCHOOL_PASSWORD='secret'\nnot a pair\n"
    )
    assert parsed == {
        "SMARTSCHOOL_USERNAME": "student",
        "SMARTSCHOOL_PASSWORD": "secret",
    }


def test_load_config_refuses_mixed_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / "config.env"
    env_file.write_text(
        "SMARTSCHOOL_USERNAME=child\nSMARTSCHOOL_PASSWORD=child-secret\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SMARTSCHOOL_USERNAME", "child")
    monkeypatch.setenv("SMARTSCHOOL_PASSWORD", "parent-secret")

    with pytest.raises(_common.CredentialMixError, match="geen poging"):
        load_config(env_file)
    assert os.environ["SMARTSCHOOL_PASSWORD"] == "parent-secret"


def test_load_config_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "missing.env")


def test_open_session_loads_config_then_validates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, bool] = {}

    class _Creds:
        username = "child"
        main_url = "school.smartschool.be"

        def validate(self) -> None:
            seen["validated"] = True

    monkeypatch.setattr(_common, "load_config", lambda: None)
    monkeypatch.setattr(_common, "EnvCredentials", _Creds)
    monkeypatch.setattr(_common, "school_host", lambda _url: "school.smartschool.be")
    monkeypatch.setattr(_common, "hold_session_lock", lambda _path: None)
    missing = tmp_path / "missing"
    monkeypatch.setattr(_common, "_auth_failed_path", lambda _user: missing)
    monkeypatch.setattr(_common, "GuardedSession", lambda creds: ("session", creds))

    assert open_session()[0] == "session"
    assert seen["validated"] is True


def test_login_returns_public_user_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    session = MagicMock()
    session.creds.main_url = "school.smartschool.be"
    session.confirm_login.return_value = {
        "id": 7,
        "name": {
            "startingWithFirstName": "Alex Example",
            "startingWithLastName": "Example Alex",
        },
        "password": "nope",
        "username": "alex",
    }
    monkeypatch.setattr(login, "open_session", lambda: session)

    payload = login.build()
    assert payload["ok"] is True
    assert payload["main_url"] == "school.smartschool.be"
    assert payload["user"] == {"id": 7, "name": "Alex Example", "username": "alex"}
    assert "password" not in payload["user"]


def test_schedule_one_day_passes_planner_query(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_elements(_session: object, **kwargs: object) -> list[object]:
        captured.update(kwargs)
        period = SimpleNamespace(
            date_time_from=SimpleNamespace(strftime=lambda _fmt: "2026-09-22 08:25"),
            date_time_to=SimpleNamespace(strftime=lambda _fmt: "2026-09-22 09:15"),
            whole_day=False,
        )
        return [
            SimpleNamespace(
                name="Wiskunde",
                planned_element_type="planned-lessons",
                period=period,
                assignment_type=None,
                courses=[SimpleNamespace(name="Wiskunde")],
                locations=[SimpleNamespace(title="B1.02")],
                organisers=None,
                unconfirmed=False,
                pinned=False,
                color="#fff",
            )
        ]

    monkeypatch.setattr(schedule, "PlannedElements", fake_elements)
    monkeypatch.setattr(schedule, "open_session", lambda: object())

    payload = schedule.build(["--date", "2026-09-22", "--types", " planned-lessons "])
    assert payload["date"] == "2026-09-22"
    assert payload["total"] == 1
    assert payload["elements"][0]["name"] == "Wiskunde"
    assert payload["elements"][0]["locations"] == ["B1.02"]
    assert captured["from_date"] == date(2026, 9, 22)
    assert captured["till_date"] == date(2026, 9, 22)
    assert captured["types"] == "planned-lessons"
    assert captured["includes"] is None


def test_schedule_offset_and_days_ahead() -> None:
    args = schedule.parse_args(["--offset", "1", "--days-ahead", "6"])
    start, end = schedule.resolve_range(args, today=date(2026, 9, 22))
    assert start == date(2026, 9, 23)
    assert end == date(2026, 9, 29)


def test_schedule_rejects_inverted_range() -> None:
    args = schedule.parse_args(["--from", "2026-09-22", "--to", "2026-09-01"])
    with pytest.raises(ValueError, match="before start"):
        schedule.resolve_range(args)


def test_messages_filters_sender_and_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    headers = [
        SimpleNamespace(
            id=1,
            from_="Jansen",
            subject="Huiswerk",
            date=date(2026, 9, 1),
            unread=True,
            priority=None,
            attachments=0,
        ),
        SimpleNamespace(
            id=2,
            from_="Secretariaat",
            subject="Melding",
            date=date(2026, 9, 2),
            unread=False,
            priority=None,
            attachment=2,
        ),
    ]
    monkeypatch.setattr(messages, "open_session", lambda: object())
    monkeypatch.setattr(messages, "MessageHeaders", lambda *_a, **_k: headers)

    payload = messages.build(["--sender", "jans", "--limit", "10"])
    assert payload["pagination"]["total"] == 1
    assert payload["messages"][0]["subject"] == "Huiswerk"
    assert "body" not in payload["messages"][0]
    assert payload["filters"]["box_type"] == "INBOX"


def test_messages_unknown_box() -> None:
    payload = messages.build(["--box", "ARCHIVE"])
    assert "error" in payload
    assert "ARCHIVE" in payload["error"]


def test_messages_reads_one_id(monkeypatch: pytest.MonkeyPatch) -> None:
    full = SimpleNamespace(
        id=9,
        from_="Jansen",
        subject="Toets",
        date=date(2026, 9, 3),
        unread=False,
        priority=None,
        attachments=0,
        body="Vergeet je boek niet",
    )
    monkeypatch.setattr(messages, "open_session", lambda: object())
    monkeypatch.setattr(
        messages,
        "Message",
        lambda *_a, **_k: SimpleNamespace(get=lambda: full),
    )

    payload = messages.build(["--id", "9"])
    assert payload["id"] == 9
    assert payload["body"] == "Vergeet je boek niet"


def test_results_course_filter_skips_details(monkeypatch: pytest.MonkeyPatch) -> None:
    def make(course: str, touched: list[str]) -> SimpleNamespace:
        class _Result(SimpleNamespace):
            @property
            def details(self) -> object:
                touched.append(course)
                return SimpleNamespace(central_tendencies=[])

        return _Result(
            courses=[SimpleNamespace(name=course)],
            name="Toets",
            gradebook_owner=SimpleNamespace(
                name=SimpleNamespace(starting_with_first_name="Jan")
            ),
            period=SimpleNamespace(name="P1"),
            graphic=SimpleNamespace(
                description="8/10",
                value=8,
                achieved_points=8,
                total_points=10,
                percentage=80,
            ),
            date=date(2026, 9, 1),
            availability_date=date(2026, 9, 2),
            does_count=True,
            feedback=[SimpleNamespace(text="goed")],
        )

    touched: list[str] = []
    monkeypatch.setattr(results, "open_session", lambda: object())
    monkeypatch.setattr(
        results,
        "Results",
        lambda _session: [make("Wiskunde", touched), make("Frans", touched)],
    )

    payload = results.build(["--course", "wisk", "--no-details", "--limit", "5"])
    assert payload["pagination"]["total"] == 1
    assert payload["results"][0]["course"] == "Wiskunde"
    assert payload["results"][0]["feedback"] == "goed"
    assert "average" not in payload["results"][0]
    assert touched == []


def test_courses_lists_teachers(monkeypatch: pytest.MonkeyPatch) -> None:
    teacher = SimpleNamespace(name=SimpleNamespace(starting_with_last_name="Peeters"))
    monkeypatch.setattr(courses, "open_session", lambda: object())
    monkeypatch.setattr(
        courses,
        "Courses",
        lambda _session: [SimpleNamespace(name="Aardrijkskunde", teachers=[teacher])],
    )

    payload = courses.build()
    assert payload["total"] == 1
    assert payload["courses"][0]["teachers"] == ["Peeters"]


def test_planned_element_empty_period() -> None:
    row = planned_element(SimpleNamespace(name="", courses=None, locations=None))
    assert row["from"] is None
    assert row["courses"] == []


def test_plugin_manifest_has_no_mcp_server() -> None:
    manifest = json.loads(
        (PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
    )
    assert manifest["name"] == "smartschool"
    assert manifest["author"]["name"] == "Sonic Rocket / Mark"
    assert "mcpServers" not in manifest
    assert not (PLUGIN / ".mcp.json").exists()

    skill = (PLUGIN / "skills" / "smartschool" / "SKILL.md").read_text(encoding="utf-8")
    script_names = (
        "login.py",
        "schedule.py",
        "messages.py",
        "results.py",
        "courses.py",
    )
    for script in script_names:
        assert script in skill
    assert "config.example.env" in skill
    assert "niet opnieuw proberen" in skill
    assert "in parallel" in skill
    readme = (PLUGIN / "README.md").read_text(encoding="utf-8")
    assert "--plugin-dir" in readme
    assert "config.example.env" in readme
    assert "auth_failed" in readme


def test_wrong_password_posts_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    posts: list[str] = []
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    class _Resp:
        url = "https://school.smartschool.be/login"

    def _super_login(self, response):
        posts.append(response.url)
        return _Resp()

    monkeypatch.setattr(Smartschool, "_do_login", _super_login)
    session = _guard(tmp_path)
    with pytest.raises(SmartSchoolAuthenticationError, match="niet opnieuw proberen"):
        session._do_login(_Resp())
    assert posts == ["https://school.smartschool.be/login"]
    with pytest.raises(SmartSchoolAuthenticationError, match="niet opnieuw proberen"):
        session._do_login(_Resp())
    assert len(posts) == 1


def test_second_run_blocked_by_auth_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    failed = tmp_path / ".cache" / "smartschool" / "child" / "auth_failed"
    failed.parent.mkdir(parents=True)
    failed.write_text("login failed\n", encoding="utf-8")
    monkeypatch.setattr(_common, "load_config", lambda: None)
    monkeypatch.setattr(_common, "hold_session_lock", lambda _path: None)

    class _Creds:
        username = "child"
        password = "x"
        main_url = "school.smartschool.be"
        mfa = "2014-01-02"

        def validate(self) -> None:
            return None

    monkeypatch.setattr(_common, "EnvCredentials", _Creds)
    with pytest.raises(SmartSchoolAuthenticationError, match="auth_failed"):
        open_session()


def test_wrong_host_does_not_call_super(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    called: list[str] = []

    def _super_login(self, response):
        called.append("posted")
        return response

    monkeypatch.setattr(Smartschool, "_do_login", _super_login)
    session = _guard(tmp_path)
    evil = SimpleNamespace(url="https://evil.example/login")
    with pytest.raises(SmartSchoolAuthenticationError, match="not posted"):
        session._do_login(evil)
    assert called == []
    assert not (tmp_path / ".cache" / "smartschool" / "child" / "auth_failed").exists()


def test_session_lock_is_exclusive(tmp_path: Path) -> None:
    import fcntl
    import os

    hold_session_lock(tmp_path)
    fd = os.open(tmp_path / ".session.lock", os.O_RDWR)
    try:
        with pytest.raises(BlockingIOError):
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(fd)


def _guard(tmp_path: Path) -> _common.GuardedSession:
    creds = SimpleNamespace(
        username="child",
        password="wrong",
        main_url="school.smartschool.be",
        mfa="2014-01-02",
    )

    def _validate(self) -> None:
        return None

    creds.validate = _validate.__get__(creds)
    monkey_home = tmp_path
    assert monkey_home.exists()
    return _common.GuardedSession(creds)
