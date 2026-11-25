"""The ochtendbriefing and weekbriefing prompts.

Claude Desktop only accepts a ``prompts/get`` answer that matches the
template in the extension's ``manifest.json``, so the server's text and the
manifest's text must be identical.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import smartschool_mcp.server as srv
from smartschool_mcp import prompts

ROOT = Path(__file__).resolve().parent.parent
NAMES = ("ochtendbriefing", "weekbriefing")


def _manifest_prompts() -> dict[str, dict]:
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    return {p["name"]: p for p in manifest.get("prompts", [])}


def _server_tools() -> set[str]:
    source = (ROOT / "smartschool_mcp" / "server.py").read_text(encoding="utf-8")
    return set(re.findall(r"@mcp\.tool\(\)\ndef (\w+)\(", source))


@pytest.mark.asyncio
async def test_server_offers_both_prompts() -> None:
    listed = {p.name for p in await srv.mcp.list_prompts()}

    assert set(NAMES) <= listed


@pytest.mark.asyncio
@pytest.mark.parametrize("name", NAMES)
async def test_server_text_matches_the_manifest(name: str) -> None:
    result = await srv.mcp.get_prompt(name)
    text = result.messages[0].content.text  # type: ignore[union-attr]

    assert text == _manifest_prompts()[name]["text"]


@pytest.mark.parametrize("name", NAMES)
def test_prompt_only_names_tools_the_server_has(name: str) -> None:
    text = prompts.BRIEFINGS[name]
    named = set(re.findall(r"\b((?:get|download)_[a-z_]+)\b", text))

    assert named
    assert named <= _server_tools(), named - _server_tools()


@pytest.mark.parametrize("name", NAMES)
def test_prompt_is_read_only(name: str) -> None:
    text = prompts.BRIEFINGS[name]

    assert "Alleen lezen" in text
    assert "Log nooit zelf in" in text


@pytest.mark.parametrize("name", NAMES)
def test_prompt_drops_empty_sections_entirely(name: str) -> None:
    """A heading with "Geen taken" under it is noise on a phone screen."""
    text = prompts.BRIEFINGS[name]

    assert "zonder inhoud laat je volledig weg" in text.lower()
