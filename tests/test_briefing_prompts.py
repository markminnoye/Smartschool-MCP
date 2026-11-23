"""MCP ochtend-/weekbriefing prompt templates."""

from __future__ import annotations

import json
from pathlib import Path

from smartschool_mcp.briefing_prompts import ochtendbriefing_text, weekbriefing_text

ROOT = Path(__file__).resolve().parent.parent


def test_ochtendbriefing_names_child_and_tools() -> None:
    text = ochtendbriefing_text("Lisa")
    assert "Lisa" in text
    assert "get_schedule" in text
    assert "get_planned_elements" in text
    assert "Vanavond zeker" in text
    assert "Alleen lezen" in text
    assert "elliot" not in text.lower()
    assert "mark.minnoye@" not in text.lower()


def test_ochtendbriefing_without_child_uses_configure() -> None:
    text = ochtendbriefing_text("")
    assert "Configure" in text
    assert "get_children" in text


def test_weekbriefing_is_weekly_not_morning_standup() -> None:
    text = weekbriefing_text("Sofie")
    assert "Sofie" in text
    assert "weekbriefing" in text.lower() or "schoolweek" in text
    assert "Deze week" in text
    assert "Meebrengen" in text
    assert "geen ochtend-standup" in text
    assert "get_planned_elements" in text


def test_manifest_declares_both_prompts() -> None:
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    prompts = {p["name"]: p for p in manifest["prompts"]}
    assert set(prompts) == {"ochtendbriefing", "weekbriefing"}
    for name in ("ochtendbriefing", "weekbriefing"):
        assert prompts[name]["arguments"] == ["child"]
        assert "${arguments.child}" in prompts[name]["text"]
        assert "get_children" in prompts[name]["text"]
        assert "elliot" not in prompts[name]["text"].lower()
