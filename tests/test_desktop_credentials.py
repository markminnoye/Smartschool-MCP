"""Configure dialog seeds the central store once. No Smartschool login."""

from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from smartschool_mcp.credentials import (
    INCOMPLETE_DESKTOP_CREDENTIALS,
    NO_DESKTOP_CREDENTIALS,
    prepare_server_credentials,
    save_credentials,
)

_ENV_KEYS = (
    "SMARTSCHOOL_USER",
    "SMARTSCHOOL_USERNAME",
    "SMARTSCHOOL_PASSWORD",
    "SMARTSCHOOL_MAIN_URL",
    "SMARTSCHOOL_MFA",
    "SMARTSCHOOL_PROFILE",
    "SMARTSCHOOL_CHILD",
    "SMARTSCHOOL_CHILD_NAME",
    "GROK_PLUGIN_DATA",
    "SMARTSCHOOL_CONFIG",
)


def _reload_server():
    import smartschool_mcp.server as srv

    module = importlib.reload(srv)
    module._env_session.cache_clear()
    return module


@pytest.fixture
def isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("smartschool_mcp.credentials.keychain_enabled", lambda: False)
    monkeypatch.setattr(
        "smartschool_mcp.credentials._legacy_credential_files", lambda: []
    )
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    return tmp_path


def _dialog(
    monkeypatch: pytest.MonkeyPatch, *, password: str = "dialog-secret"
) -> None:
    monkeypatch.setenv("SMARTSCHOOL_MAIN_URL", "dering")
    monkeypatch.setenv("SMARTSCHOOL_USERNAME", "parent")
    monkeypatch.setenv("SMARTSCHOOL_PASSWORD", password)
    monkeypatch.setenv("SMARTSCHOOL_MFA", "2014-03-21")
    monkeypatch.setenv("SMARTSCHOOL_CHILD_NAME", "Emma")


def _stored_password(root: Path) -> str:
    path = root / ".config" / "smartschool" / "credentials.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    return str(document["profiles"][0]["password"])


def test_dialog_is_saved_once_and_then_used(
    isolated_store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _dialog(monkeypatch)
    srv = _reload_server()
    seen: dict[str, str] = {}
    saves = {"n": 0}
    real_save = save_credentials

    def _open() -> str:
        seen["user"] = os.environ["SMARTSCHOOL_USERNAME"]
        seen["password"] = os.environ["SMARTSCHOOL_PASSWORD"]
        seen["child"] = os.environ["SMARTSCHOOL_CHILD"]
        return "session"

    def _save(*args: Any, **kwargs: Any) -> Path:
        saves["n"] += 1
        return real_save(*args, **kwargs)

    with (
        patch.object(srv, "_open_env_session", side_effect=_open),
        patch.object(srv, "GuardedSession", side_effect=AssertionError("login")),
        patch("smartschool_mcp.credentials.save_credentials", side_effect=_save),
    ):
        assert srv._env_session() == "session"
        srv._env_session.cache_clear()
        assert srv._env_session() == "session"

    assert saves["n"] == 1
    assert seen == {
        "user": "parent",
        "password": "dialog-secret",
        "child": "Emma",
    }
    assert _stored_password(isolated_store) == "dialog-secret"
    document = json.loads(
        (isolated_store / ".config" / "smartschool" / "credentials.json").read_text(
            encoding="utf-8"
        )
    )
    assert len(document["profiles"]) == 1


def test_existing_profile_wins_over_the_dialog(
    isolated_store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    save_credentials("parent", "stored-secret", "dering", "2014-03-21", "Emma")
    _dialog(monkeypatch, password="dialog-secret")
    with patch(
        "smartschool_mcp.credentials.save_credentials",
        side_effect=AssertionError("second copy"),
    ):
        prepare_server_credentials()

    assert os.environ["SMARTSCHOOL_PASSWORD"] == "stored-secret"
    assert _stored_password(isolated_store) == "stored-secret"
    assert "dialog-secret" not in (
        isolated_store / ".config" / "smartschool" / "credentials.json"
    ).read_text(encoding="utf-8")


def test_missing_store_returns_the_dutch_configure_message(
    isolated_store: Path,
) -> None:
    srv = _reload_server()
    with patch.object(srv, "GuardedSession", side_effect=AssertionError("login")):
        result = srv.get_courses()
    assert result == [{"error": NO_DESKTOP_CREDENTIALS}]
    assert "Please verify" not in result[0]["error"]


def test_incomplete_dialog_names_the_missing_setup(
    isolated_store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SMARTSCHOOL_USERNAME", "parent")
    srv = _reload_server()
    with patch.object(srv, "GuardedSession", side_effect=AssertionError("login")):
        result = srv.get_courses()
    assert result == [{"error": INCOMPLETE_DESKTOP_CREDENTIALS}]
    assert "parent" not in result[0]["error"]


def test_several_profiles_explain_how_to_pick(isolated_store: Path) -> None:
    save_credentials("parent", "stored-secret", "dering", "2014-03-21", "Emma")
    save_credentials("parent", "other-secret", "depass", "2014-03-21", "Noah")
    for key in _ENV_KEYS:
        os.environ.pop(key, None)
    srv = _reload_server()
    with patch.object(srv, "GuardedSession", side_effect=AssertionError("login")):
        result = srv.get_courses()
    message = result[0]["error"]
    assert message.startswith("Meerdere profielen")
    assert "Configure" in message
    assert "stored-secret" not in message
    assert "other-secret" not in message


def test_unsubstituted_placeholders_count_as_empty(
    isolated_store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SMARTSCHOOL_MAIN_URL", "${user_config.school}")
    monkeypatch.setenv("SMARTSCHOOL_USERNAME", "${user_config.username}")
    monkeypatch.setenv("SMARTSCHOOL_PASSWORD", "${user_config.password}")
    monkeypatch.setenv("SMARTSCHOOL_MFA", "${user_config.birth_date}")
    monkeypatch.setenv("SMARTSCHOOL_CHILD_NAME", "${user_config.child_name}")
    srv = _reload_server()
    result = srv.get_courses()
    assert result == [{"error": NO_DESKTOP_CREDENTIALS}]
    store = isolated_store / ".config" / "smartschool" / "credentials.json"
    assert not store.exists()
