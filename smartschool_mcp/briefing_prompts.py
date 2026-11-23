"""Dutch parent briefing prompts for Claude Desktop / MCP clients.

Claude Desktop validates that ``prompts/get`` returns the exact
``manifest.json`` template after substituting ``${arguments.*}``.
Any other wording is rejected as possible prompt injection.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_ARG_CHILD = "${arguments.child}"


@lru_cache(maxsize=1)
def _manifest_prompts() -> dict[str, str]:
    data = json.loads((_ROOT / "manifest.json").read_text(encoding="utf-8"))
    return {p["name"]: p["text"] for p in data.get("prompts", [])}


def render_manifest_prompt(name: str, *, child: str = "") -> str:
    """Return the manifest template with ``${arguments.child}`` substituted."""
    try:
        template = _manifest_prompts()[name]
    except KeyError as exc:
        raise KeyError(f"Unknown prompt in manifest.json: {name}") from exc
    return template.replace(_ARG_CHILD, child.strip())


def ochtendbriefing_text(child: str = "") -> str:
    """Daily standup prompt; must match the Desktop manifest template."""
    return render_manifest_prompt("ochtendbriefing", child=child)


def weekbriefing_text(child: str = "") -> str:
    """Weekly overview prompt; must match the Desktop manifest template."""
    return render_manifest_prompt("weekbriefing", child=child)
