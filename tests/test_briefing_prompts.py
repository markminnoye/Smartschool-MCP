"""MCP ochtend-/weekbriefing prompt templates must match the mcpb manifest."""

from __future__ import annotations

import json
from pathlib import Path

from smartschool_mcp.briefing_prompts import (
    ochtendbriefing_text,
    render_manifest_prompt,
    weekbriefing_text,
)

ROOT = Path(__file__).resolve().parent.parent


def _manifest_prompt(name: str) -> dict:
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    prompts = {p["name"]: p for p in manifest["prompts"]}
    return prompts[name]


def test_manifest_declares_both_prompts() -> None:
    for name in ("ochtendbriefing", "weekbriefing"):
        prompt = _manifest_prompt(name)
        assert prompt["arguments"] == ["child"]
        assert "${arguments.child}" in prompt["text"]
        assert "get_children" in prompt["text"]
        assert "elliot" not in prompt["text"].lower()


def test_prompts_read_study_material_through_planner_attachments() -> None:
    """The planner lists never carry study material; the detail call does."""
    for name in ("ochtendbriefing", "weekbriefing"):
        text = _manifest_prompt(name)["text"]
        assert "get_planner_attachments" in text
        assert "get_reports" in text
        assert "published_date" in text
        assert "Log nooit zelf in" in text


def test_evening_section_is_renamed() -> None:
    text = _manifest_prompt("ochtendbriefing")["text"]
    assert "Vanavond te doen" in text
    assert "Vanavond zeker" not in text


def test_runtime_text_matches_manifest_substitution() -> None:
    """Claude Desktop rejects prompts/get that diverge from the template."""
    for name, render in (
        ("ochtendbriefing", ochtendbriefing_text),
        ("weekbriefing", weekbriefing_text),
    ):
        template = _manifest_prompt(name)["text"]
        for child in ("", "Lisa", "Sofie"):
            expected = template.replace("${arguments.child}", child)
            assert render(child) == expected
            assert len(render(child)) == len(expected)


def test_render_helper_matches_named_wrappers() -> None:
    assert render_manifest_prompt("ochtendbriefing", child="Ann") == (
        ochtendbriefing_text("Ann")
    )
    assert "get_planned_elements" in weekbriefing_text("")
