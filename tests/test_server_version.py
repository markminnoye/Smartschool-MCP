"""The server tells clients which version is running."""

from __future__ import annotations

import re
from pathlib import Path

import smartschool_mcp
import smartschool_mcp.server as srv

ROOT = Path(__file__).resolve().parent.parent


def test_server_reports_the_package_version() -> None:
    assert srv.mcp.version == smartschool_mcp.__version__


def test_package_version_matches_pyproject() -> None:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text)

    assert match is not None
    assert smartschool_mcp.__version__ == match.group(1)
