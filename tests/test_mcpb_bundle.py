"""The Claude Desktop bundle (``.mcpb``) stays in step with the server.

No network: the tests stage the bundle tree but do not run the
``mcpb`` CLI.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import scripts.build_mcpb as build_mcpb

ROOT = Path(__file__).resolve().parent.parent


def _manifest() -> dict:
    return json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))


def _project_version() -> str:
    return build_mcpb.project_version()


def test_manifest_lists_every_server_tool() -> None:
    source = (ROOT / "smartschool_mcp" / "server.py").read_text(encoding="utf-8")
    tools = re.findall(r"@mcp\.tool\(\)\ndef (\w+)\(", source)

    assert tools
    assert [tool["name"] for tool in _manifest()["tools"]] == tools


def test_manifest_version_matches_the_project() -> None:
    assert _manifest()["version"] == _project_version()


def test_manifest_runs_the_server_over_stdio_with_the_env_credentials() -> None:
    server = _manifest()["server"]

    assert server["type"] == "uv"
    assert server["entry_point"] == "smartschool_mcp/__main__.py"
    env = server["mcp_config"]["env"]
    assert env["MCP_TRANSPORT"] == "stdio"
    assert set(env) >= {
        "SMARTSCHOOL_MAIN_URL",
        "SMARTSCHOOL_USERNAME",
        "SMARTSCHOOL_PASSWORD",
        "SMARTSCHOOL_MFA",
    }


def test_password_is_masked_and_birth_date_shown() -> None:
    user_config = _manifest()["user_config"]

    assert user_config["password"]["sensitive"] is True
    # Shown, not masked: a typo in the birth date costs a login attempt.
    assert user_config["mfa"].get("sensitive") is not True
    assert "dd-mm-yyyy" in user_config["mfa"]["title"]
    assert user_config["school"].get("sensitive") is not True


def test_staged_bundle_holds_the_package_and_the_lockfile(tmp_path: Path) -> None:
    staging = tmp_path / "bundle"

    build_mcpb.stage_bundle(staging)

    for name in (
        "manifest.json",
        "icon.png",
        "pyproject.toml",
        "uv.lock",
        "README.md",
        "LICENSE",
    ):
        assert (staging / name).is_file(), name
    manifest = _manifest()
    assert manifest["icon"] == "icon.png"
    assert manifest["icons"]
    for entry in manifest["icons"]:
        assert (staging / entry["src"]).is_file(), entry["src"]
    assert (staging / "smartschool_mcp" / "server.py").is_file()
    assert not list(staging.rglob("__pycache__"))
    assert not (staging / "tests").exists()


def test_staging_stamps_the_release_version(tmp_path: Path) -> None:
    staging = tmp_path / "bundle"

    stamped = build_mcpb.stage_bundle(staging, version="v9.8.7")

    assert stamped == "9.8.7"
    data = json.loads((staging / "manifest.json").read_text(encoding="utf-8"))
    assert data["version"] == "9.8.7"
    init = (staging / "smartschool_mcp" / "__init__.py").read_text(encoding="utf-8")
    assert '__version__ = "9.8.7"' in init


@pytest.mark.parametrize("bad", ["", "v", "1.0; rm -rf /", "../1.0"])
def test_invalid_versions_are_refused(bad: str) -> None:
    with pytest.raises(SystemExit):
        build_mcpb.normalize_version(bad)
