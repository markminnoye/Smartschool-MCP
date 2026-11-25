# Smartschool MCP Development Guide

This is an MCP (Model Context Protocol) server that connects Claude and other AI assistants to Smartschool accounts. The server supports both stdio (Claude Desktop) and streamable-http (claude.ai remote) transports.

## Commands

```bash
# Install all dependencies (including dev)
uv sync --extra dev

# Run all tests
uv run pytest

# Run a single test
uv run pytest tests/test_tools.py::test_get_courses_returns_list

# Run with coverage
uv run pytest --cov=smartschool_mcp --cov-report=term-missing

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

### Core structure

The package is a single-file MCP server (`smartschool_mcp/server.py`) plus entry-point modules:

- **`server.py`** — all MCP tool definitions with `@mcp.tool()` decorators; uses `MCPServer("Smartschool MCP")` as the server instance
- **`__main__.py`** — CLI entry point handling transport selection (stdio vs streamable-http)
- **`auth.py`** — OAuth 2.1 provider for universal mode (multi-user)
- **`main.py`** — backward-compatibility shim (deprecated)

### Transport modes

`__main__.py` handles two transports selected via `--transport` (or `MCP_TRANSPORT` env var):

- **`stdio`** (default) — used by Claude Desktop; calls `mcp.run()` directly
- **`streamable-http`** — used by claude.ai remote integrations; builds a Starlette ASGI app via `mcp.streamable_http_app()`, wraps it in `CORSMiddleware` (required so claude.ai can read `Mcp-Session-Id`), then optionally wraps that in `_BearerAuthMiddleware` (enabled by setting `MCP_API_KEY`). Served with uvicorn on `MCP_HOST:MCP_PORT`.

### Single-user vs. Universal mode

- **Single-user** — credentials in environment variables (`SMARTSCHOOL_USERNAME`, `SMARTSCHOOL_PASSWORD`, etc.); `_env_session()` returns a lazy-cached `Smartschool(EnvCredentials())` instance
- **Universal** — OAuth 2.1 flow with browser-based login form (`auth.py`); `_cached_app_session()` returns TTL-cached sessions per unique credential tuple (maxsize=256, TTL=`SESSION_TTL_SECONDS` default 3600)

### Session management

- `_session()` in `server.py` routes to either `_env_session()` (single-user) or `_cached_app_session()` (universal) based on whether the MCP request context contains OAuth credentials
- Single-user sessions use `@lru_cache(maxsize=1)` for lifetime-of-process caching
- Universal sessions use `@cached` with `TTLCache` to expire stale cookies automatically
- Lazy initialization — startup never touches the network; missing credentials surface as tool-level errors, not crashes

### `smartschool` library integration

The upstream library (`github.com/svaningelgem/smartschool`, pinned via `uv.lock`) exposes lazy-loaded objects. Accessing `.details` on a result, for example, triggers an HTTP request. Many attributes use `getattr(..., default)` defensively because the library's type stubs are incomplete.

**Every tool wraps its body in `try/except Exception as e` and returns `{"error": f"...{e!s}"}` (or a list variant) so failures surface as tool results rather than MCP errors.**

### Internal helpers

- `_safe_format_date(obj)` — converts any date/datetime to `"YYYY-MM-DD"` string; returns `None` on failure
- `_safe_get_teacher_names(teachers, use_last_name)` — extracts names from teacher objects; returns `[]` on failure
- `_TaskDict`, `_CourseDict`, `_DayDict` — TypedDicts used internally in `get_future_tasks` for mypy correctness

## Adding a new tool

1. Find the corresponding class in the [smartschool library](https://github.com/svaningelgem/smartschool)
2. Add the import to `server.py`
3. Decorate with `@mcp.tool()` and call `_session()` inside the function body
4. Wrap the entire body in `try / except Exception as e` and return `{"error": f"Failed to ...: {e}"}` (or `[{"error": ...}]` for list returns)
5. Use built-in generics (`list[dict[str, Any]]`, `str | None`) — not `typing.List` / `typing.Optional`
6. Add tests in `tests/test_tools.py`
7. Update `CHANGELOG.md` and the `README.md` features section

## Testing conventions

Tests never hit the network. The `autouse` `mock_session` fixture in `conftest.py` patches `_session`. Three test files:

- **`test_helpers.py`** — pure unit tests for `_safe_format_date` and `_safe_get_teacher_names`
- **`test_middleware.py`** — async tests for `_BearerAuthMiddleware` (valid/invalid tokens, OPTIONS bypass, non-HTTP scopes)
- **`test_tools.py`** — error-handling tests (every tool must catch exceptions) and happy-path tests using `MagicMock` return values

### Writing tests

- All tests run without real credentials — use the `mock_session` fixture from `conftest.py`
- Mark any test that needs real credentials with `@pytest.mark.integration`
- Test **error paths** too: patch the relevant smartschool class to raise an exception and assert the returned `{"error": "..."}` dict

## HTTP transport changes

All HTTP logic lives in `_run_http()` inside `__main__.py`. Keep uvicorn and starlette imports **inside** `_run_http` so that the stdio startup path stays lightweight.

## Versioning and releases

Bump `version` in three places (they must match):
- `pyproject.toml`
- `smartschool_mcp/__init__.py`
- `server.json`

Releases are fully automated via GitHub Actions — create a GitHub Release (tag `vX.Y.Z`) and the `publish.yml` workflow builds and publishes to PyPI + MCP Registry.

## Commit conventions

Use [Conventional Commits](https://www.conventionalcommits.org/):

| Prefix | When to use |
|--------|-------------|
| `feat:` | New tool or feature |
| `fix:` | Bug fix |
| `docs:` | Documentation only |
| `refactor:` | Code change with no behaviour change |
| `test:` | Adding or fixing tests |
| `ci:` | CI/CD changes |
| `chore:` | Dependency bumps, tooling |

**Breaking changes** must include `BREAKING CHANGE:` in the commit footer (or `!` after the prefix: `feat!:`).
