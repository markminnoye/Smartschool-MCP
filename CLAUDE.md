# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install all dependencies (including dev)
uv sync --extra dev

# Run all tests
uv run pytest

# Run a single test
uv run pytest tests/test_tools.py::test_get_courses_returns_list

# Lint
uv run ruff check .

# Format (check only)
uv run ruff format --check .

# Format (apply)
uv run ruff format .

# Type check
uv run mypy smartschool_mcp/
```

CI runs lint → typecheck → tests (Python 3.10–3.13) in that order. All three must pass before merging.

## Architecture

The package is an MCP server (`smartschool_mcp/server.py`), an entry-point module (`smartschool_mcp/__main__.py`), and `smartschool_mcp/content.py`: pure parsers for planner element details and the server-rendered leerpaden/exercises pages, tested on anonymised responses in `tests/fixtures/`.

### Transport modes

`__main__.py` handles two transports selected via `--transport` (or `MCP_TRANSPORT` env var):

- **`stdio`** (default) — used by Claude Desktop; calls `mcp.run()` directly.
- **`streamable-http`** — used by claude.ai remote integrations; builds a Starlette ASGI app via `mcp.streamable_http_app()`, wraps it in `CORSMiddleware` (required so claude.ai can read `Mcp-Session-Id`), then optionally wraps that in `_BearerAuthMiddleware` (enabled by setting `MCP_API_KEY`). Served with uvicorn on `MCP_HOST:MCP_PORT`.

### Tool registration

All MCP tools live in `server.py` and are registered with `@mcp.tool()` decorators on plain Python functions. `MCPServer("Smartschool MCP")` is the server instance (`mcp`). Adding a new tool means writing a new `@mcp.tool()` function — no registration table to update.

### Session and credentials

`_session()` picks a backend and returns a live session. In universal (OAuth) mode it resolves per-request credentials through `_cached_app_session`; otherwise it falls back to `_env_session`, which builds a `Smartschool(EnvCredentials())`. Both are lazy on purpose — startup never touches the network, so missing credentials surface as tool-level errors rather than process crashes. In tests, `conftest.py` patches `_session` with an `autouse` fixture so no real credentials are needed.

Both caches are `cachetools.TTLCache`s bounded by `SESSION_TTL_SECONDS` (default 3600), because Smartschool expires cookies server-side and a stdio process otherwise lives for days on one session object.

`_session()` then routes through `_ensure_live_session()`, which issues one `GET /` per cached session. This is load-bearing: `Smartschool.ensure_authenticated()` only checks whether an `authenticated_user` record exists, and the library restores that record from `<cache>/authenticated_user.yml` on every new instance — so an expired cookie still looks authenticated. REST endpoints self-heal (they redirect to `/login`, which `Session.request` handles transparently), but the XML dispatcher behind messages, attachments and future tasks answers an unauthenticated POST with an **empty 200**, which the library reads as an empty result set. The probe turns that silent empty into either a real re-login or a loud `AuthenticationError`. Its outcome is memoised on the session object so a failure is not retried on every tool call — repeated failed logins can lock a real Smartschool account.

### `smartschool` library

The upstream library (`github.com/svaningelgem/smartschool`, pinned via `uv.lock`) exposes lazy-loaded objects. Accessing `.details` on a result, for example, triggers an HTTP request. Many attributes use `getattr(..., default)` defensively because the library's type stubs are incomplete. Every tool wraps its body in `try/except Exception as e` and returns `{"error": f"...{e!s}"}` (or a list variant) so failures surface as tool results rather than MCP errors.

### Internal helpers

- `_safe_format_date(obj)` — converts any date/datetime to `"YYYY-MM-DD"` string; returns `None` on failure.
- `_safe_get_teacher_names(teachers, use_last_name)` — extracts names from teacher objects; returns `[]` on failure.
- `_TaskDict`, `_CourseDict`, `_DayDict` — TypedDicts used internally in `get_future_tasks` for mypy correctness.

### Tests

Tests never hit the network. The `autouse` `mock_session` fixture in `conftest.py` patches `_session`. Three test files:
- `test_helpers.py` — pure unit tests for `_safe_format_date` and `_safe_get_teacher_names`.
- `test_middleware.py` — async tests for `_BearerAuthMiddleware` (valid/invalid tokens, OPTIONS bypass, non-HTTP scopes).
- `test_tools.py` — error-handling tests (every tool must catch exceptions) and happy-path tests using `MagicMock` return values.

### `main.py`

Top-level `main.py` is a backward-compatibility shim; all real logic is in the `smartschool_mcp` package.
