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
def mock_session() -> MagicMock:
    """Replace the cached Smartschool session with a MagicMock.

    Applied automatically to every test in this suite.  Tests that need to
    configure specific return values can request this fixture explicitly.
    """
    mock = MagicMock()
    with patch("smartschool_mcp.server._session", return_value=mock):
        yield mock
