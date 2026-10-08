"""The Claude Desktop bundle manifest matches the server and the fork pin."""

from __future__ import annotations

import json
import re
import struct
from pathlib import Path

import scripts.build_mcpb as build_mcpb

ROOT = Path(__file__).resolve().parent.parent


def _manifest() -> dict:
    return json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))


def _server_tool_names() -> list[str]:
    source = (ROOT / "smartschool_mcp" / "server.py").read_text(encoding="utf-8")
    return re.findall(r"@mcp\.tool\(\)\ndef (\w+)\(", source)


def test_manifest_configure_saves_into_the_central_store() -> None:
    manifest = _manifest()
    assert manifest["manifest_version"] == "0.4"
    assert manifest["version"] == "0.2.0"
    assert manifest["server"]["type"] == "uv"
    assert manifest["server"]["entry_point"] == "smartschool_mcp/__main__.py"
    config = manifest["server"]["mcp_config"]
    assert config["command"] == "uv"
    assert "python" in config["args"]
    assert "-m" in config["args"]
    assert "smartschool_mcp" in config["args"]
    assert config["env"] == {
        "MCP_TRANSPORT": "stdio",
        "SMARTSCHOOL_MAIN_URL": "${user_config.school}",
        "SMARTSCHOOL_USERNAME": "${user_config.username}",
        "SMARTSCHOOL_PASSWORD": "${user_config.password}",
        "SMARTSCHOOL_MFA": "${user_config.birth_date}",
        "SMARTSCHOOL_CHILD_NAME": "${user_config.child_name}",
    }
    user_config = manifest["user_config"]
    assert set(user_config) == {
        "school",
        "username",
        "password",
        "birth_date",
        "child_name",
    }
    for field in user_config.values():
        assert field["type"] == "string"
        assert field["required"] is False
        assert field["title"]
        assert field["description"]
    assert user_config["password"]["sensitive"] is True
    assert user_config["birth_date"]["sensitive"] is True
    assert user_config["birth_date"]["title"] == "Geboortedatum van je kind (jjjj-mm-dd)"
    assert "jjjj-mm-dd" in user_config["birth_date"]["title"]
    assert user_config["child_name"]["title"] == "Naam van je kind"
    assert user_config["school"].get("sensitive") is not True
    assert manifest["display_name"] == "Smartschool voor ouders"
    assert manifest["author"] == {
        "name": "Sonic Rocket",
        "url": "https://github.com/markminnoye/Smartschool-MCP",
    }
    repo = "https://github.com/markminnoye/Smartschool-MCP"
    assert manifest["homepage"] == repo
    assert manifest["documentation"] == f"{repo}/blob/main/README.md"
    assert manifest["support"] == f"{repo}/issues"
    assert manifest["repository"]["url"] == repo
    assert manifest["icon"] == "icon.png"
    assert "MauroDruwel" not in json.dumps(manifest)
    assert "Elliot" not in manifest["long_description"]
    assert "de geboortedatum van je kind" in manifest["long_description"]
    icon = ROOT / "icon.png"
    header = icon.read_bytes()[:24]
    assert header[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", header[16:24])
    assert (width, height) == (512, 512)


def test_manifest_tools_match_the_server() -> None:
    names = _server_tool_names()
    assert names
    assert [tool["name"] for tool in _manifest()["tools"]] == names


def test_bundle_pyproject_vendors_the_pinned_fork() -> None:
    text = build_mcpb.bundle_pyproject()
    assert build_mcpb.SMARTSCHOOL_REV.startswith("517de70")
    assert build_mcpb.SMARTSCHOOL_REV in text
    assert 'path = "vendor/smartschool"' in text
    assert "git =" not in text
    build_mcpb.assert_repo_pin(ROOT)


def test_dutch_parent_guide_lists_tools_and_lockout() -> None:
    doc = (ROOT / "docs" / "claude-desktop-test.md").read_text(encoding="utf-8")
    for name in _server_tool_names():
        assert f"`{name}`" in doc
    assert "auth_failed" in doc
    assert "niet opnieuw proberen" in doc
    assert "error=1" in doc
    assert "credentials.json" in doc
    assert "Configure" in doc
    assert "vijf velden" not in doc
    assert "Ga naar map" in doc
    lowered = doc.lower()
    assert "terminal" not in lowered
    assert "git clone" not in lowered
    assert "uv run" not in lowered
