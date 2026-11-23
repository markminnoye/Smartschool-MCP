"""
Shared pytest fixtures for the Smartschool MCP test suite.

All unit tests run without real Smartschool credentials.
The ``mock_session`` fixture patches the lazy ``_session()`` singleton so no
network connections are made during the test run.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def download_root(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """Point every download at this test's tmp_path, never ~/Downloads."""
    monkeypatch.setenv("SMARTSCHOOL_DOWNLOAD_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture(autouse=True)
def mock_session(request: pytest.FixtureRequest) -> MagicMock:
    """Replace the cached Smartschool session with a MagicMock.

    Applied automatically to every test in this suite.  Tests that need to
    configure specific return values can request this fixture explicitly.
    Integration tests talk to a real session (or a subprocess) and skip the
    patch.
    """
    if request.node.get_closest_marker("integration"):
        yield MagicMock()
        return
    mock = MagicMock()
    with patch("smartschool_mcp.server._session", return_value=mock):
        yield mock
