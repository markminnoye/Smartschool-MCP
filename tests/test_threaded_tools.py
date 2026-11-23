"""Tools run in a worker thread, one at a time per Smartschool login."""

from __future__ import annotations

import threading
import time
from unittest.mock import patch

import anyio
from mcp.server.auth.middleware.auth_context import (
    auth_context_var,
    get_access_token,
)
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from smartschool import AppCredentials

import smartschool_mcp.auth as auth
import smartschool_mcp.server as srv


def test_every_registered_tool_is_async() -> None:
    tools = srv.mcp._tool_manager.list_tools()
    assert tools
    assert all(tool.is_async for tool in tools)


def test_tool_schema_keeps_original_parameters() -> None:
    tool = srv.mcp._tool_manager.get_tool("get_results")
    assert tool is not None
    assert set(tool.parameters["properties"]) == {
        "limit",
        "offset",
        "course_filter",
        "include_details",
        "include_raw",
    }
    assert tool.description and "Retrieve student results" in tool.description


def test_module_level_tools_stay_sync() -> None:
    with patch("smartschool_mcp.server.Courses", return_value=[]):
        assert srv.get_courses() == []


async def test_tool_runs_off_the_event_loop_thread() -> None:
    loop_thread = threading.get_ident()
    seen: dict[str, int] = {}

    def fake_courses(_session: object) -> list[object]:
        seen["thread"] = threading.get_ident()
        return []

    with patch("smartschool_mcp.server.Courses", side_effect=fake_courses):
        await srv.mcp.call_tool("get_courses", {})

    assert seen["thread"] != loop_thread


async def test_event_loop_keeps_running_while_a_tool_blocks() -> None:
    order: list[str] = []

    def slow(_session: object) -> list[object]:
        time.sleep(0.2)
        return []

    async def run_tool() -> None:
        await srv.mcp.call_tool("get_courses", {})
        order.append("tool")

    async def ticker() -> None:
        for _ in range(3):
            await anyio.sleep(0.01)
        order.append("ticker")

    with patch("smartschool_mcp.server.Courses", side_effect=slow):
        async with anyio.create_task_group() as group:
            group.start_soon(run_tool)
            group.start_soon(ticker)

    assert order == ["ticker", "tool"]


def _peak_concurrency(keys: list[object]) -> int:
    active = 0
    peak = 0
    guard = threading.Lock()

    def slow(_session: object) -> list[object]:
        nonlocal active, peak
        with guard:
            active += 1
            peak = max(peak, active)
        time.sleep(0.05)
        with guard:
            active -= 1
        return []

    async def main() -> None:
        async with anyio.create_task_group() as group:
            for _ in keys:
                group.start_soon(srv.mcp.call_tool, "get_courses", {})

    with (
        patch("smartschool_mcp.server.Courses", side_effect=slow),
        patch("smartschool_mcp.server._session_lock_key", side_effect=list(keys)),
        patch.dict(srv._session_locks, clear=True),
    ):
        anyio.run(main)
    return peak


def test_calls_for_one_login_run_one_at_a_time() -> None:
    assert _peak_concurrency(["env", "env", "env"]) == 1


def test_calls_for_different_logins_run_in_parallel() -> None:
    assert _peak_concurrency([("app", "a", "x"), ("app", "b", "x")]) == 2


def _token(cred_key: str) -> auth.SmartschoolAccessToken:
    return auth.SmartschoolAccessToken(
        token="t-" + cred_key,
        client_id="client",
        scopes=[],
        expires_at=None,
        cred_key=cred_key,
    )


async def test_access_token_reaches_the_worker_thread() -> None:
    token = _token("k1")
    reset = auth_context_var.set(AuthenticatedUser(token))
    try:
        seen = await srv._threaded(get_access_token)()
    finally:
        auth_context_var.reset(reset)
    assert seen is token


def test_lock_key_is_per_login_not_per_token() -> None:
    creds = AppCredentials(
        username="kid", password="pw", main_url="school.smartschool.be", mfa=""
    )
    with patch.dict(auth._credential_store, {"k1": creds, "k2": creds}):
        keys = []
        for cred_key in ("k1", "k2"):
            reset = auth_context_var.set(AuthenticatedUser(_token(cred_key)))
            try:
                keys.append(srv._session_lock_key())
            finally:
                auth_context_var.reset(reset)

    assert keys[0] == keys[1] == ("app", "kid", "school.smartschool.be")


def test_lock_key_without_oauth_is_env() -> None:
    assert srv._session_lock_key() == "env"
