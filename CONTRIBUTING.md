# Contributing to Smartschool MCP

Thank you for your interest in contributing! This document explains how to get
started, what we expect, and how the project works.

---

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Development Setup](#development-setup)
- [Project Structure](#project-structure)
- [Making Changes](#making-changes)
- [Tests](#tests)
- [Commit & PR Conventions](#commit--pr-conventions)
- [Release Process](#release-process)

---

## Code of Conduct

This project follows the [Contributor Covenant v2.1](CODE_OF_CONDUCT.md).
Please read it before participating.

---

## Getting Started

1. **Fork** the repository and clone your fork:
   ```bash
   git clone https://github.com/<your-username>/Smartschool-MCP.git
   cd Smartschool-MCP
   ```

2. **Install [uv](https://docs.astral.sh/uv/)** (if you don't have it):
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

3. **Install all dependencies**, including dev tools:
   ```bash
   uv sync --extra dev
   ```

4. **Install pre-commit hooks** (runs linters automatically on `git commit`):
   ```bash
   uv run pre-commit install
   ```

---

## Development Setup

### Environment Variables (for manual testing only)

Running tools against a real Smartschool account requires credentials.
Copy the example and fill it in — **never commit `.env`**:

```bash
cp .env.example .env   # if it exists, otherwise create it manually
```

```dotenv
SMARTSCHOOL_USERNAME=your_username
SMARTSCHOOL_PASSWORD=your_password
SMARTSCHOOL_MAIN_URL=your-school.smartschool.be
SMARTSCHOOL_MFA=YYYY-MM-DD
```

### Running the server locally

```bash
# stdio mode (Claude Desktop)
uv run smartschool-mcp

# HTTP mode (claude.ai / remote)
uv run smartschool-mcp --transport streamable-http --port 8000
```

---

## Project Structure

```
smartschool_mcp/
├── __init__.py      # Package version
├── __main__.py      # CLI entry point (stdio & HTTP transport)
└── server.py        # FastMCP server + all tool definitions

tests/
├── conftest.py      # Shared fixtures (mock_session)
├── test_helpers.py  # Unit tests for pure helper functions
├── test_tools.py    # Tests for tool registration + error handling
└── test_middleware.py  # Tests for BearerAuthMiddleware

.github/
├── workflows/
│   ├── ci.yml       # Lint, typecheck, test matrix (Python 3.10–3.13)
│   └── mcpb.yml     # Pack the Claude Desktop .mcpb; attach it to a release
├── ISSUE_TEMPLATE/  # Structured bug/feature templates
└── PULL_REQUEST_TEMPLATE.md
```

---

## Making Changes

### Adding a new tool

1. Find the corresponding class in the
   [smartschool library](https://github.com/svaningelgem/smartschool).
2. Add the import to `server.py`.
3. Decorate with `@_tool` (registers it with `mcp` and runs it in a worker thread, one call at a time per login) and call `_session()` inside the function body.
4. Wrap the entire body in `try / except Exception as e` and return
   `{"error": f"Failed to ...: {e}"}` (or `[{"error": ...}]` for list returns).
5. Use built-in generics (`list[dict[str, Any]]`, `str | None`) — not
   `typing.List` / `typing.Optional`.
6. Add tests in `tests/test_tools.py`.
7. Update `CHANGELOG.md` and the `README.md` features section.

### Changing the HTTP transport

All HTTP logic lives in `_run_http()` inside `__main__.py`. Keep uvicorn and
starlette imports **inside** `_run_http` so that the stdio startup path stays
lightweight.

---

## Tests

```bash
# Run all unit tests (no credentials needed)
uv run pytest

# Run with coverage
uv run pytest --cov=smartschool_mcp --cov-report=term-missing

# Live read-only catalog smoke (same login+cookies as the MCP; never in CI)
uv run python scripts/smoke_portal.py              # phase 1 legacy library, then GETs
uv run python scripts/smoke_portal.py --legacy-only
PORTAL_SMOKE=1 uv run pytest -m integration        # same run via pytest
```

`.env` with `SMARTSCHOOL_*` is loaded if present (never commit it). Writes, auth, CSRF, and unmapped XML POSTs are skipped. The table prints status and item counts only — no response bodies.

### Writing tests

- All tests run without real credentials — use the `mock_session` fixture
  from `conftest.py`.
- Mark any test that needs real credentials with `@pytest.mark.integration`.
  Those tests must also require `PORTAL_SMOKE=1` so a local `uv run pytest`
  with credentials in the environment still never hits Smartschool.
- Test **error paths** too: patch the relevant smartschool class to raise an
  exception and assert the returned `{"error": "..."}` dict.

---

## Commit & PR Conventions

We use [Conventional Commits](https://www.conventionalcommits.org/):

| Prefix | When to use |
|--------|-------------|
| `feat:` | New tool or feature |
| `fix:` | Bug fix |
| `docs:` | Documentation only |
| `refactor:` | Code change with no behaviour change |
| `test:` | Adding or fixing tests |
| `ci:` | CI/CD changes |
| `chore:` | Dependency bumps, tooling |

**Breaking changes** must include `BREAKING CHANGE:` in the commit footer
(or `!` after the prefix: `feat!:`).

PR titles should also follow this format — the CI checks it.

---

## Release Process

The only distribution channel is the Claude Desktop `.mcpb` on GitHub
Releases. Nothing is published to PyPI or the MCP Registry.

1. Bump `version` in `pyproject.toml`, `manifest.json`,
   `scripts/build_mcpb.py` (`_DEFAULT_VERSION`), `smartschool_mcp/__init__.py`
   and `claude-plugin/.claude-plugin/plugin.json`, and run `uv lock`.
2. Update `CHANGELOG.md`: move `[Unreleased]` entries under a new version heading.
3. Open a PR, get it merged to `main`.
4. Create a GitHub Release (tag `vX.Y.Z`) — the `mcpb.yml` workflow builds
   `smartschool-mcp-<version>.mcpb` and attaches it to that release.
