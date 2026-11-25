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
├── __main__.py      # CLI entry point (stdio & streamable-http transports)
├── auth.py          # OAuth 2.1 provider (universal mode)
└── server.py        # MCPServer + all tool definitions

tests/
├── conftest.py      # Shared fixtures (mock_session)
├── test_helpers.py  # Unit tests for pure helper functions
├── test_tools.py    # Tests for tool registration + error handling
└── test_middleware.py  # Tests for BearerAuthMiddleware

.github/
├── workflows/
│   ├── ci.yml       # Lint, typecheck, test matrix (Python 3.10–3.13)
│   └── publish.yml  # Build, publish to PyPI + MCP Registry on release
├── ISSUE_TEMPLATE/  # Structured bug/feature templates
└── PULL_REQUEST_TEMPLATE.md
```

---

## Making Changes

### Adding a new tool

1. Find the corresponding class in the
   [smartschool library](https://github.com/svaningelgem/smartschool).
   [`docs/portal-api/`](docs/portal-api/README.md) maps the website's calls to
   library classes and existing tools.
2. Add the import to `server.py`.
3. Decorate with `@mcp.tool()` and call `_session()` inside the function body.
4. Wrap the entire body in `try / except Exception as e` and return
   `{"error": f"Failed to ...: {e}"}` (or `[{"error": ...}]` for list returns).
5. Use built-in generics (`list[dict[str, Any]]`, `str | None`) — not
   `typing.List` / `typing.Optional`.
6. Add tests in `tests/test_tools.py`.
7. Update `CHANGELOG.md` and the `README.md` features section.
8. Add the tool to `tools` in `manifest.json` (the Claude Desktop extension);
   `tests/test_mcpb_bundle.py` fails until it is listed there.

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

# Run only integration tests (requires real credentials in env)
uv run pytest -m integration
```

### Writing tests

- All tests run without real credentials — use the `mock_session` fixture
  from `conftest.py`.
- Mark any test that needs real credentials with `@pytest.mark.integration`.
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

Releases are fully automated via GitHub Actions:

1. Bump `version` in `pyproject.toml`, `smartschool_mcp/__init__.py`, and
   `server.json` — all three must match.
2. Update `CHANGELOG.md`: move `[Unreleased]` entries under a new version heading.
3. Open a PR, get it merged to `main`.
4. Create a GitHub Release (tag `vX.Y.Z`) — the `publish.yml` workflow then:
   - Builds the wheel and sdist with `uv build`
   - Publishes to PyPI via OIDC trusted publishing (no API key needed)
   - Publishes to the MCP Registry
