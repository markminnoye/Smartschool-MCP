"""Unit tests for the Claude Code plugin scripts. No network."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import date, datetime, timedelta, timezone
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


def test_example_env_points_at_the_shared_store() -> None:
    text = (PLUGIN / "config.example.env").read_text(encoding="utf-8")
    parsed = parse_env_file(text)
    assert "SMARTSCHOOL_PASSWORD" not in parsed
    assert "SMARTSCHOOL_MFA" not in parsed
    assert "credentials.json" in text
    assert ".config/smartschool" in text


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

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("GROK_PLUGIN_DATA", raising=False)
    monkeypatch.delenv("SMARTSCHOOL_USER", raising=False)
    monkeypatch.setenv("SMARTSCHOOL_USERNAME", "child")
    monkeypatch.setenv("SMARTSCHOOL_PASSWORD", "child-secret")
    monkeypatch.setenv("SMARTSCHOOL_MAIN_URL", "school.smartschool.be")
    monkeypatch.setenv("SMARTSCHOOL_MFA", "2014-01-02")
    monkeypatch.setattr(_common, "load_config", lambda: None)
    monkeypatch.setattr(_common, "EnvCredentials", _Creds)
    monkeypatch.setattr(_common, "school_host", lambda _url: "school.smartschool.be")
    monkeypatch.setattr(_common, "hold_session_lock", lambda _path: None)
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
    session.child_names = ["Alex"]
    session.profile_id = "school:alex"
    monkeypatch.setattr(login, "open_sessions", lambda: [session])

    payload = login.build([])
    assert payload["ok"] is True
    assert payload["main_url"] == "school.smartschool.be"
    assert payload["user"] == {"id": 7, "name": "Alex Example", "username": "alex"}
    assert payload["child"] == "Alex"
    assert payload["children"] == ["Alex"]
    assert payload["profile"] == "school:alex"
    assert "password" not in payload["user"]


def test_schedule_one_day_passes_planner_query(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_calendar(
        _session: object, start: date, end: date, types: object, includes: object
    ) -> list[dict]:
        captured["start"] = start
        captured["end"] = end
        captured["types"] = types
        captured["includes"] = includes
        return [
            {
                "id": "0a13e756-656a-5053-925e-7aad5db31a87",
                "name": "Wiskunde",
                "description": "",
                "type": "planned-lessons",
                "locations": ["B1.02"],
                "upload_folders": [],
            }
        ]

    monkeypatch.setattr(schedule, "fetch_calendar", fake_calendar)
    monkeypatch.setattr(schedule, "open_sessions", lambda: [object()])

    payload = schedule.build(["--date", "2026-09-22", "--types", " planned-lessons "])
    assert payload["date"] == "2026-09-22"
    assert payload["total"] == 1
    assert payload["elements"][0]["name"] == "Wiskunde"
    assert payload["elements"][0]["locations"] == ["B1.02"]
    assert captured["start"] == date(2026, 9, 22)
    assert captured["end"] == date(2026, 9, 22)
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
    monkeypatch.setattr(
        messages,
        "open_sessions",
        lambda: [SimpleNamespace(confirm_login=lambda: {})],
    )
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
    monkeypatch.setattr(
        messages,
        "open_sessions",
        lambda: [SimpleNamespace(confirm_login=lambda: {})],
    )
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
    monkeypatch.setattr(results, "open_sessions", lambda: [object()])
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
    monkeypatch.setattr(courses, "open_sessions", lambda: [object()])
    monkeypatch.setattr(
        courses,
        "Courses",
        lambda _session: [SimpleNamespace(name="Aardrijkskunde", teachers=[teacher])],
    )

    payload = courses.build([])
    assert payload["total"] == 1
    assert payload["courses"][0]["teachers"] == ["Peeters"]


def test_planned_element_empty_period() -> None:
    row = planned_element(SimpleNamespace(name="", courses=None, locations=None))
    assert row["from"] is None
    assert row["courses"] == []
    assert row["id"] is None
    assert row["description"] == ""
    assert row["upload_folders"] == []


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
    assert "GROK_PLUGIN_DATA" in readme
    assert "GROK_PLUGIN_DATA" in skill
    assert "--reset" in skill
    assert "--profile" in skill
    assert "Geen loginpoging" in skill
    assert "Mijn kinderen" in skill


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
    failed = tmp_path / ".cache" / "smartschool" / "school" / "child" / "auth_failed"
    failed.parent.mkdir(parents=True)
    failed.write_text("login failed\n", encoding="utf-8")
    monkeypatch.delenv("GROK_PLUGIN_DATA", raising=False)
    monkeypatch.delenv("SMARTSCHOOL_USER", raising=False)
    monkeypatch.setenv("SMARTSCHOOL_USERNAME", "child")
    monkeypatch.setenv("SMARTSCHOOL_PASSWORD", "x")
    monkeypatch.setenv("SMARTSCHOOL_MAIN_URL", "school.smartschool.be")
    monkeypatch.setenv("SMARTSCHOOL_MFA", "2014-01-02")
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


def _isolate_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("GROK_PLUGIN_DATA", raising=False)
    monkeypatch.delenv("SMARTSCHOOL_CONFIG", raising=False)
    monkeypatch.delenv("SMARTSCHOOL_KEYCHAIN", raising=False)
    for key in (
        "SMARTSCHOOL_USER",
        "SMARTSCHOOL_USERNAME",
        "SMARTSCHOOL_PASSWORD",
        "SMARTSCHOOL_MAIN_URL",
        "SMARTSCHOOL_MFA",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr("smartschool_mcp.credentials.sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("smartschool_mcp.credentials.keychain_enabled", lambda: False)

    def legacy() -> list[Path]:
        explicit = os.environ.get("SMARTSCHOOL_CONFIG", "").strip()
        if not explicit:
            return []
        chosen = Path(explicit)
        if not chosen.is_file():
            raise FileNotFoundError(f"Credential file not found: {chosen}")
        return [chosen]

    monkeypatch.setattr("smartschool_mcp.credentials._legacy_credential_files", legacy)


def _account(**overrides: str) -> dict[str, str]:
    account = {
        "main_url": "school.smartschool.be",
        "username": "student",
        "password": "file-secret",
        "mfa": "2014-01-02",
    }
    account.update(overrides)
    return account


def _write_account(path: Path, account: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(account), encoding="utf-8")


def test_env_user_wins_over_saved_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate_home(monkeypatch, tmp_path)
    _write_account(
        tmp_path / ".config" / "smartschool" / "credentials.json",
        _account(username="from-file", password="file-secret"),
    )
    monkeypatch.setenv("SMARTSCHOOL_USER", "from-env")
    monkeypatch.setenv("SMARTSCHOOL_PASSWORD", "env-secret")

    _common.ensure_credentials()

    assert os.environ["SMARTSCHOOL_USERNAME"] == "from-env"
    assert os.environ["SMARTSCHOOL_PASSWORD"] == "env-secret"
    assert os.environ["SMARTSCHOOL_MAIN_URL"] == "school.smartschool.be"
    assert os.environ["SMARTSCHOOL_MFA"] == "2014-01-02"
    assert "file-secret" not in os.environ["SMARTSCHOOL_PASSWORD"]


def test_saved_file_uses_grok_plugin_data_before_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate_home(monkeypatch, tmp_path)
    grok = tmp_path / "grok-data"
    _write_account(grok / "credentials.json", _account(username="grok-user"))
    _write_account(
        tmp_path / ".config" / "smartschool" / "credentials.json",
        _account(username="home-user", password="home-secret"),
    )
    monkeypatch.setenv("GROK_PLUGIN_DATA", str(grok))

    _common.ensure_credentials()

    assert os.environ["SMARTSCHOOL_USERNAME"] == "grok-user"
    assert os.environ["SMARTSCHOOL_PASSWORD"] == "file-secret"
    assert "home-secret" not in os.environ["SMARTSCHOOL_PASSWORD"]


def test_env_host_overrides_saved_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate_home(monkeypatch, tmp_path)
    _write_account(
        tmp_path / ".config" / "smartschool" / "credentials.json",
        _account(main_url="file.smartschool.be"),
    )
    monkeypatch.setenv("SMARTSCHOOL_MAIN_URL", "env.smartschool.be")

    _common.ensure_credentials()

    assert os.environ["SMARTSCHOOL_USERNAME"] == "student"
    assert os.environ["SMARTSCHOOL_MAIN_URL"] == "env.smartschool.be"


def test_config_env_used_when_store_is_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate_home(monkeypatch, tmp_path)
    env_file = tmp_path / "config.env"
    env_file.write_text(
        "SMARTSCHOOL_MAIN_URL=school.smartschool.be\n"
        "SMARTSCHOOL_USERNAME=child\n"
        "SMARTSCHOOL_PASSWORD=child-secret\n"
        "SMARTSCHOOL_MFA=2014-01-02\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SMARTSCHOOL_CONFIG", str(env_file))

    _common.ensure_credentials()

    assert os.environ["SMARTSCHOOL_USERNAME"] == "child"
    assert os.environ["SMARTSCHOOL_PASSWORD"] == "child-secret"


def test_missing_credentials_without_tty_does_not_open_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _isolate_home(monkeypatch, tmp_path)
    opened: list[object] = []
    monkeypatch.setattr(_common, "GuardedSession", lambda creds: opened.append(creds))

    with pytest.raises(_common.MissingCredentialsError, match="Geen loginpoging"):
        open_session()
    assert opened == []

    def build() -> dict[str, str]:
        open_session()
        return {"ok": True}

    with pytest.raises(SystemExit) as exc:
        _common.main(build)
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exc.value.code == 1
    assert "Geen loginpoging gedaan" in payload["error"]
    assert "SMARTSCHOOL_PASSWORD" in payload["error"]
    assert captured.err == ""


def test_prompt_writes_owner_only_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _isolate_home(monkeypatch, tmp_path)
    monkeypatch.setattr("smartschool_mcp.credentials.sys.stdin.isatty", lambda: True)
    answers = iter(["dering", "student", "Emma"])
    monkeypatch.setattr("builtins.input", lambda: next(answers))
    secrets = iter(["pw-secret-value", "21/03/2014"])
    monkeypatch.setattr(
        "smartschool_mcp.credentials.getpass.getpass",
        lambda _prompt="": next(secrets),
    )

    _common.ensure_credentials()
    captured = capsys.readouterr()

    path = tmp_path / ".config" / "smartschool" / "credentials.json"
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    stored = json.loads(path.read_text(encoding="utf-8"))
    profile = stored["profiles"][0]
    assert profile["username"] == "student"
    assert profile["main_url"] == "dering.smartschool.be"
    assert profile["mfa"] == "2014-03-21"
    assert profile["children"] == [{"name": "Emma"}]
    assert profile["password"] == "pw-secret-value"
    assert os.environ["SMARTSCHOOL_PASSWORD"] == "pw-secret-value"
    assert "pw-secret-value" not in captured.out
    assert "pw-secret-value" not in captured.err


def test_keychain_file_stores_username_and_host_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate_home(monkeypatch, tmp_path)
    monkeypatch.setattr("smartschool_mcp.credentials.keychain_enabled", lambda: True)
    secrets: dict[tuple[str, str], str] = {}

    def fake_set(service: str, account: str, secret: str) -> None:
        secrets[(service, account)] = secret

    def fake_get(service: str, account: str) -> str | None:
        return secrets.get((service, account))

    monkeypatch.setattr("smartschool_mcp.credentials.keychain_set", fake_set)
    monkeypatch.setattr("smartschool_mcp.credentials.keychain_get", fake_get)

    path = _common.save_credentials(
        "student", "s3cret-pass", "school", "02/01/2014", "Emma"
    )
    text = path.read_text(encoding="utf-8")
    assert "s3cret-pass" not in text
    assert "2014-01-02" not in text
    stored = json.loads(text)
    assert stored["profiles"] == [
        {
            "main_url": "school.smartschool.be",
            "username": "student",
            "children": [{"name": "Emma"}],
        }
    ]
    assert secrets[(_common._KEYCHAIN_SERVICE, "student@school.smartschool.be")] == (
        "s3cret-pass"
    )

    loaded = _common.read_saved_credentials()
    assert loaded is not None
    assert loaded["password"] == "s3cret-pass"
    assert loaded["mfa"] == "2014-01-02"


def test_keychain_failure_hides_the_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(
        args: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        assert args[0] == "security"
        assert "add-generic-password" in args
        return subprocess.CompletedProcess(args, 1, "", "rejected s3cret-pass")

    monkeypatch.setattr("smartschool_mcp.credentials.subprocess.run", fake_run)
    with pytest.raises(_common.CredentialStoreError, match="Sleutelhanger") as exc:
        _common.keychain_set(
            "smartschool-mcp",
            "student@school.smartschool.be",
            "s3cret-pass",
        )
    assert "s3cret-pass" not in str(exc.value)


def test_user_alias_mismatch_stops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate_home(monkeypatch, tmp_path)
    monkeypatch.setenv("SMARTSCHOOL_USER", "child")
    monkeypatch.setenv("SMARTSCHOOL_USERNAME", "parent")
    monkeypatch.setenv("SMARTSCHOOL_PASSWORD", "env-secret")
    with pytest.raises(_common.CredentialMixError, match="geen poging"):
        _common.ensure_credentials()


def test_expired_session_drops_cookies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    cache = tmp_path / ".cache" / "smartschool" / "child"
    cache.mkdir(parents=True)
    cookies = cache / "cookies.txt"
    cookies.write_text("session-cookie\n", encoding="utf-8")
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    (cache / "session.json").write_text(
        json.dumps({"expires_at": past}), encoding="utf-8"
    )

    _common.expire_stale_session(cache)

    assert not cookies.exists()
    assert not (cache / "session.json").exists()


def test_fresh_session_keeps_cookies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    cache = tmp_path / ".cache" / "smartschool" / "child"
    cache.mkdir(parents=True)
    cookies = cache / "cookies.txt"
    cookies.write_text("session-cookie\n", encoding="utf-8")
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    (cache / "session.json").write_text(
        json.dumps({"expires_at": future}), encoding="utf-8"
    )

    _common.expire_stale_session(cache)

    assert cookies.read_text(encoding="utf-8") == "session-cookie\n"


def test_cookie_without_expiry_is_dropped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    cache = tmp_path / ".cache" / "smartschool" / "child"
    cache.mkdir(parents=True)
    (cache / "cookies.txt").write_text("old\n", encoding="utf-8")

    _common.expire_stale_session(cache)

    assert not (cache / "cookies.txt").exists()


def test_stamp_session_sets_expiry_without_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("SMARTSCHOOL_SESSION_TTL", "120")
    cache = tmp_path / ".cache" / "smartschool" / "child"
    cache.mkdir(parents=True)
    (cache / "cookies.txt").write_text("session-cookie\n", encoding="utf-8")

    _common.stamp_session(cache)

    meta = json.loads((cache / "session.json").read_text(encoding="utf-8"))
    assert set(meta) == {"expires_at"}
    expires = datetime.fromisoformat(meta["expires_at"])
    assert expires > datetime.now(timezone.utc)
    assert (cache / "session.json").stat().st_mode & 0o777 == 0o600
    assert (cache / "cookies.txt").stat().st_mode & 0o777 == 0o600
    assert "session-cookie" not in (cache / "session.json").read_text(encoding="utf-8")


def test_reset_clears_store_and_cache_without_login(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate_home(monkeypatch, tmp_path)
    path = tmp_path / ".config" / "smartschool" / "credentials.json"
    _write_account(path, _account(password="wipe-me-secret"))
    cache = tmp_path / ".cache" / "smartschool" / "school" / "student"
    cache.mkdir(parents=True)
    (cache / "cookies.txt").write_text("session-cookie\n", encoding="utf-8")
    (cache / "auth_failed").write_text("login failed\n", encoding="utf-8")
    opened: list[object] = []

    def _refuse_open() -> None:
        opened.append("session")
        raise AssertionError("reset must not open a session")

    monkeypatch.setattr(login, "open_sessions", _refuse_open)

    payload = login.build(["--reset"])

    assert payload["ok"] is True
    assert payload["cleared"] is True
    assert payload["saved"] is False
    assert payload["profile"] == "school:student"
    assert payload["child"] is None
    assert "wipe-me-secret" not in json.dumps(payload)
    assert not path.exists()
    assert not cache.exists()
    assert opened == []


def test_reset_on_tty_saves_new_account_without_login(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _isolate_home(monkeypatch, tmp_path)
    monkeypatch.setattr("smartschool_mcp.credentials.sys.stdin.isatty", lambda: True)
    answers = iter(["north", "student", "Liam"])
    monkeypatch.setattr("builtins.input", lambda: next(answers))
    secrets = iter(["new-secret-value", "2014-01-02"])
    monkeypatch.setattr(
        "smartschool_mcp.credentials.getpass.getpass",
        lambda _prompt="": next(secrets),
    )
    opened: list[object] = []

    def _refuse_open() -> None:
        opened.append("session")
        raise AssertionError("reset must not open a session")

    monkeypatch.setattr(login, "open_sessions", _refuse_open)

    payload = login.build(["--reset"])
    captured = capsys.readouterr()

    assert payload["ok"] is True
    assert payload["saved"] is True
    assert opened == []
    stored = json.loads(
        (tmp_path / ".config" / "smartschool" / "credentials.json").read_text(
            encoding="utf-8"
        )
    )
    assert stored["profiles"][0]["password"] == "new-secret-value"
    assert stored["profiles"][0]["main_url"] == "north.smartschool.be"
    assert stored["profiles"][0]["children"] == [{"name": "Liam"}]
    assert "new-secret-value" not in json.dumps(payload)
    assert "new-secret-value" not in captured.out
    assert "new-secret-value" not in captured.err


def test_main_redacts_password_and_mfa(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("SMARTSCHOOL_PASSWORD", "super-secret-value")
    monkeypatch.setenv("SMARTSCHOOL_MFA", "2014-01-02")

    def build() -> dict[str, str]:
        raise RuntimeError("leaked super-secret-value and 2014-01-02")

    with pytest.raises(SystemExit) as exc:
        _common.main(build)
    payload = json.loads(capsys.readouterr().out)
    assert exc.value.code == 1
    assert "super-secret-value" not in payload["error"]
    assert "2014-01-02" not in payload["error"]
    assert "***" in payload["error"]


def test_unreadable_credential_file_hides_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate_home(monkeypatch, tmp_path)
    path = tmp_path / ".config" / "smartschool" / "credentials.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"password": "s3cret-pass"', encoding="utf-8")

    with pytest.raises(_common.CredentialStoreError, match="onleesbaar") as exc:
        _common.read_saved_credentials()
    assert "s3cret-pass" not in str(exc.value)


def test_school_accepts_subdomain_and_url() -> None:
    assert _common.normalize_school("DeRing") == "dering.smartschool.be"
    assert _common.normalize_school("north.smartschool.be") == "north.smartschool.be"
    assert (
        _common.normalize_school("https://north.smartschool.be/login")
        == "north.smartschool.be"
    )
    with pytest.raises(SmartSchoolAuthenticationError, match="subdomein"):
        _common.normalize_school("evil.com")


def test_birth_date_matches_library_yyyy_mm_dd() -> None:
    assert _common.normalize_birth_date("2010-05-15") == "2010-05-15"
    assert _common.normalize_birth_date("21/03/2014") == "2014-03-21"
    assert _common.normalize_birth_date("21.03.2014") == "2014-03-21"
    with pytest.raises(_common.CredentialStoreError, match="jjjj-mm-dd") as exc:
        _common.normalize_birth_date("JBSWY3DPEHPK3PXP")
    assert "JBSWY3DPEHPK3PXP" not in str(exc.value)


def test_profiles_are_selected_and_reset_one_at_a_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate_home(monkeypatch, tmp_path)
    _common.save_credentials("emma", "emma-secret", "dering", "2014-03-21", "Emma")
    _common.save_credentials("liam", "liam-secret", "depass", "2015-04-02", "Liam")
    dering_cache = tmp_path / ".cache" / "smartschool" / "dering" / "emma"
    depass_cache = tmp_path / ".cache" / "smartschool" / "depass" / "liam"
    dering_cache.mkdir(parents=True)
    depass_cache.mkdir(parents=True)
    (dering_cache / "cookies.txt").write_text("dering-cookie\n", encoding="utf-8")
    (depass_cache / "cookies.txt").write_text("depass-cookie\n", encoding="utf-8")

    monkeypatch.setenv("SMARTSCHOOL_PROFILE", "dering")
    selected = _common.resolve_accounts()
    assert [account["username"] for account in selected] == ["emma"]
    assert selected[0]["children"] == [{"name": "Emma"}]

    monkeypatch.delenv("SMARTSCHOOL_PROFILE")
    both = _common.resolve_accounts()
    assert [account["username"] for account in both] == ["emma", "liam"]

    refused = login.build(["--reset"])
    assert refused["ok"] is False
    assert "Niets gewist" in refused["error"]
    assert [row["child"] for row in refused["profiles"]] == ["Emma", "Liam"]
    assert "emma-secret" not in json.dumps(refused)
    assert "liam-secret" not in json.dumps(refused)
    assert dering_cache.exists()

    cleared = login.build(["--reset", "--profile", "dering"])
    assert cleared["ok"] is True
    assert cleared["profile"] == "dering:emma"
    assert cleared["child"] == "Emma"
    assert not dering_cache.exists()
    assert depass_cache.exists()
    kept = json.loads(
        (tmp_path / ".config" / "smartschool" / "credentials.json").read_text(
            encoding="utf-8"
        )
    )
    assert [profile["username"] for profile in kept["profiles"]] == ["liam"]
    assert "emma-secret" not in json.dumps(kept)


def test_legacy_account_is_copied_into_the_shared_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate_home(monkeypatch, tmp_path)
    env_file = tmp_path / "config.env"
    env_file.write_text(
        "SMARTSCHOOL_MAIN_URL=school.smartschool.be\n"
        "SMARTSCHOOL_USERNAME=child\n"
        "SMARTSCHOOL_PASSWORD=child-secret\n"
        "SMARTSCHOOL_MFA=2014-01-02\n"
        "SMARTSCHOOL_CHILD=Emma\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SMARTSCHOOL_CONFIG", str(env_file))

    _common.ensure_credentials()

    store = tmp_path / ".config" / "smartschool" / "credentials.json"
    saved = json.loads(store.read_text(encoding="utf-8"))
    assert saved["profiles"][0]["children"] == [{"name": "Emma"}]
    env_file.unlink()
    monkeypatch.delenv("SMARTSCHOOL_CONFIG", raising=False)
    for key in (
        "SMARTSCHOOL_USER",
        "SMARTSCHOOL_USERNAME",
        "SMARTSCHOOL_PASSWORD",
        "SMARTSCHOOL_MAIN_URL",
        "SMARTSCHOOL_MFA",
        "SMARTSCHOOL_CHILD",
    ):
        monkeypatch.delenv(key, raising=False)

    _common.ensure_credentials()

    assert os.environ["SMARTSCHOOL_USERNAME"] == "child"
    assert os.environ["SMARTSCHOOL_CHILD"] == "Emma"


def test_conflicting_legacy_files_are_not_merged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate_home(monkeypatch, tmp_path)
    first = tmp_path / "a.env"
    second = tmp_path / "b.env"
    body = (
        "SMARTSCHOOL_MAIN_URL=school.smartschool.be\n"
        "SMARTSCHOOL_PASSWORD=secret-value\n"
        "SMARTSCHOOL_MFA=2014-01-02\n"
    )
    first.write_text(body + "SMARTSCHOOL_USERNAME=one\n", encoding="utf-8")
    second.write_text(body + "SMARTSCHOOL_USERNAME=two\n", encoding="utf-8")
    monkeypatch.setattr(
        "smartschool_mcp.credentials._legacy_credential_files",
        lambda: [first, second],
    )

    with pytest.raises(_common.CredentialMixError, match="gedeelde"):
        _common.ensure_credentials()

    assert not (tmp_path / ".config" / "smartschool" / "credentials.json").exists()


def test_saved_children_keep_get_children_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from smartschool_mcp.credentials import load_profiles

    _isolate_home(monkeypatch, tmp_path)
    path = tmp_path / ".config" / "smartschool" / "credentials.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "profiles": [
                    {
                        "main_url": "school.smartschool.be",
                        "username": "parent",
                        "password": "parent-secret",
                        "mfa": "2014-01-02",
                        "children": [
                            {
                                "account_id": "abc-12",
                                "name": "Emma",
                                "first_name": "Emma",
                                "platform": "dering.smartschool.be",
                                "is_current": True,
                                "user_id": "9",
                            },
                            {"name": "Liam", "host": "school.smartschool.be"},
                            "Emma",
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    children = load_profiles()[0]["children"]
    assert children == [
        {
            "name": "Emma",
            "account_id": "abc-12",
            "platform": "dering.smartschool.be",
        },
        {"name": "Liam", "platform": "school.smartschool.be"},
    ]


def test_scripts_return_every_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def session(host: str, child: str, profile: str, user_id: int) -> SimpleNamespace:
        return SimpleNamespace(
            creds=SimpleNamespace(main_url=host),
            confirm_login=lambda: {"id": user_id, "username": child.lower()},
            child_names=[child],
            profile_id=profile,
        )

    monkeypatch.setattr(
        login,
        "open_sessions",
        lambda: [
            session("dering.smartschool.be", "Emma", "dering:emma", 1),
            session("north.smartschool.be", "Liam", "depass:liam", 2),
        ],
    )
    payload = login.build([])
    assert [row["child"] for row in payload["profiles"]] == ["Emma", "Liam"]
    assert payload["profiles"][0]["main_url"] == "dering.smartschool.be"
    assert "password" not in json.dumps(payload)


def _profile_auth_failed(tmp_path: Path) -> Path:
    return tmp_path / ".cache" / "smartschool" / "school" / "child" / "auth_failed"


def test_confirm_login_empty_course_list_is_not_a_lockout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    session = _guard(tmp_path)
    session.json = lambda *_a, **_k: []  # type: ignore[method-assign]

    assert session.confirm_login() == {}
    assert session._authenticated_user is not None
    assert not _profile_auth_failed(tmp_path).exists()


def test_confirm_login_empty_body_is_not_a_lockout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    session = _guard(tmp_path)
    session.json = lambda *_a, **_k: {}  # type: ignore[method-assign]

    assert session.confirm_login() == {}
    assert not _profile_auth_failed(tmp_path).exists()


def test_login_query_error_posts_once_and_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    posts: list[str] = []
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    def _super_login(self, response):
        posts.append(response.url)
        return SimpleNamespace(url="https://school.smartschool.be/login?error=1")

    monkeypatch.setattr(Smartschool, "_do_login", _super_login)
    session = _guard(tmp_path)
    with pytest.raises(SmartSchoolAuthenticationError, match="niet opnieuw proberen"):
        session._do_login(SimpleNamespace(url="https://school.smartschool.be/login"))
    assert posts == ["https://school.smartschool.be/login"]
    assert _profile_auth_failed(tmp_path).exists()
    with pytest.raises(SmartSchoolAuthenticationError, match="niet opnieuw proberen"):
        session._do_login(SimpleNamespace(url="https://school.smartschool.be/login"))
    assert len(posts) == 1


def test_verification_that_stays_on_account_page_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    def _super_verify(self, response):
        return SimpleNamespace(url="https://school.smartschool.be/account-verification")

    monkeypatch.setattr(Smartschool, "_do_login_verification", _super_verify)
    session = _guard(tmp_path)
    with pytest.raises(SmartSchoolAuthenticationError, match="niet opnieuw proberen"):
        session._do_login_verification(
            SimpleNamespace(url="https://school.smartschool.be/account-verification")
        )
    assert _profile_auth_failed(tmp_path).exists()


def test_password_post_may_continue_to_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    def _super_login(self, response):
        return SimpleNamespace(url="https://school.smartschool.be/account-verification")

    monkeypatch.setattr(Smartschool, "_do_login", _super_login)
    session = _guard(tmp_path)
    posted = session._do_login(
        SimpleNamespace(url="https://school.smartschool.be/login")
    )
    assert posted.url.endswith("/account-verification")
    assert not _profile_auth_failed(tmp_path).exists()


def test_open_session_strips_https_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, str] = {}

    class _Creds:
        username = "child"
        main_url = "https://School.Smartschool.be/login"

        def validate(self) -> None:
            return None

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("GROK_PLUGIN_DATA", raising=False)
    monkeypatch.delenv("SMARTSCHOOL_USER", raising=False)
    monkeypatch.setenv("SMARTSCHOOL_USERNAME", "child")
    monkeypatch.setenv("SMARTSCHOOL_PASSWORD", "child-secret")
    monkeypatch.setenv("SMARTSCHOOL_MAIN_URL", "school.smartschool.be")
    monkeypatch.setenv("SMARTSCHOOL_MFA", "2014-01-02")
    monkeypatch.setattr(_common, "EnvCredentials", _Creds)
    monkeypatch.setattr(_common, "hold_session_lock", lambda _path: None)
    monkeypatch.setattr(
        _common,
        "GuardedSession",
        lambda creds: captured.setdefault("main_url", creds.main_url),
    )

    open_session()
    assert captured["main_url"] == "school.smartschool.be"


def test_messages_expired_session_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    session = MagicMock()
    session.confirm_login.side_effect = SmartSchoolAuthenticationError(
        "session expired"
    )
    monkeypatch.setattr(messages, "open_sessions", lambda: [session])

    def _headers(*_a: object, **_k: object) -> list[object]:
        raise AssertionError("inbox was read")

    monkeypatch.setattr(messages, "MessageHeaders", _headers)
    with pytest.raises(SystemExit) as exc:
        messages.main(lambda: messages.build([]))
    assert exc.value.code == 1
    assert "session expired" in capsys.readouterr().out
    session.confirm_login.assert_called_once()


def test_messages_search_reraises_auth_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    headers = [
        SimpleNamespace(
            id=1,
            from_="Jansen",
            subject="Andere",
            date=date(2026, 9, 1),
            unread=False,
            priority=None,
            attachments=0,
        )
    ]
    session = MagicMock()
    session.confirm_login.return_value = {}
    monkeypatch.setattr(messages, "open_sessions", lambda: [session])
    monkeypatch.setattr(messages, "MessageHeaders", lambda *_a, **_k: headers)

    def _body(*_a: object, **_k: object) -> str:
        raise SmartSchoolAuthenticationError("session expired")

    monkeypatch.setattr(messages, "_body_text", _body)
    with pytest.raises(SmartSchoolAuthenticationError, match="session expired"):
        messages.build(["--search", "huiswerk"])


def test_results_reraises_auth_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Result(SimpleNamespace):
        @property
        def details(self) -> object:
            raise SmartSchoolAuthenticationError("session expired")

    row = _Result(
        courses=[SimpleNamespace(name="Wiskunde")],
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
        feedback=[],
    )
    monkeypatch.setattr(results, "open_sessions", lambda: [object()])
    monkeypatch.setattr(results, "Results", lambda _session: [row])
    with pytest.raises(SmartSchoolAuthenticationError, match="session expired"):
        results.build(["--limit", "5"])


def test_help_does_not_open_session(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom() -> object:
        raise AssertionError("session opened")

    monkeypatch.setattr(login, "open_sessions", _boom)
    monkeypatch.setattr(courses, "open_sessions", _boom)
    with pytest.raises(SystemExit) as login_exit:
        login.build(["--help"])
    with pytest.raises(SystemExit) as courses_exit:
        courses.build(["--help"])
    assert login_exit.value.code == 0
    assert courses_exit.value.code == 0


def test_fcntl_is_not_a_top_level_import() -> None:
    import ast

    tree = ast.parse((PLUGIN / "scripts" / "_common.py").read_text(encoding="utf-8"))
    imported = [
        alias.name
        for node in tree.body
        if isinstance(node, ast.Import)
        for alias in node.names
    ]
    assert "fcntl" not in imported
    assert "msvcrt" not in imported


def test_pyotp_is_installed() -> None:
    import pyotp

    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "pyotp>=2.9.0,<3" in text
    assert pyotp.TOTP("JBSWY3DPEHPK3PXP").now()


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
