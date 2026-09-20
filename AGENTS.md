# AGENTS.md

Shared instructions for **every** coding agent in this repository (Claude Code, Cursor, Copilot, Codex, …). Claude-specific loaders still read `CLAUDE.md`; that file only points here.

## Commands

```bash
uv sync --extra dev
uv run pytest
uv run pytest tests/test_tools.py::test_get_courses_returns_list
uv run ruff check .
uv run ruff format --check .
uv run ruff format .
uv run mypy smartschool_mcp/
```

CI: lint → typecheck → tests (Python 3.10–3.13). All three must pass before merging. Live portal smoke (`scripts/smoke_portal.py`, `pytest -m integration`) is local-only and never runs in CI.

## Which Smartschool API do we use?

There are **three** different things people call “the Smartschool API”. This MCP uses only one of them.

| Name | Who documents it | What it is | Used by this MCP? |
|------|------------------|------------|-------------------|
| **Official partner API** | [smartschool.be/developers](https://www.smartschool.be/developers/) | OAuth2 product API for *apps*: login, send messages, **write** lessons into Planner, Skore, OneRoster. No public OpenAPI for student timetable GETs. | **No** |
| **Portal REST / XML** (the website) | **Not officially documented.** Partial notes in the unofficial client: [planner.md](https://github.com/svaningelgem/smartschool/blob/master/docs/planner.md), [schedule.md](https://github.com/svaningelgem/smartschool/blob/master/docs/schedule.md) | Same HTTPS calls the logged-in browser makes (`depass.smartschool.be/planner/api/v1/…`, old Agenda XML dispatcher, …) | **Yes** — via the `smartschool` Python library |
| **School SOAP API** | Per-school `/Webservices/V3` | Separate SOAP interface with an access code | **No** |

We do **not** implement the partner API. We log in like a user and call the **same portal endpoints as the website**, wrapped by [`svaningelgem/smartschool`](https://github.com/svaningelgem/smartschool) (pinned in `uv.lock`). Until the Planner 1-to-1 branch is published, that pin is `{ git = "https://github.com/markminnoye/smartschool.git", rev = "517de70" }` (branch `planner-calendar-1to1`).

That portal surface is reverse-engineered. When Smartschool’s website adds query params or element types, the library (and then this MCP) has to catch up. Prefer **1-to-1 mapping** with those portal URLs over inventing extra MCP abstractions.

### What the website actually calls (Planner)

Observed in Chrome on `/planner/main/user/{id}/{date}`:

- `GET /planner/api/v1/planned-elements/user/{id}?from=…&to=…` — full calendar (no `types` filter): lessons, school activities, placeholders, assignments, …
- Same path **with** `types=` — sidebar subsets (to-dos, assignments, …)
- `GET /planner/api/v1/planned-elements/pinned?includes=…`

Types seen on the wire include `planned-lessons`, `planned-school-activities`, `planned-lesson-cluster-moments`, `planned-placeholders`, `planned-assignments`. The unofficial [planner.md](https://github.com/svaningelgem/smartschool/blob/master/docs/planner.md) lists only a subset (`planned-assignments`, `planned-to-dos`, `planned-placeholders`).

The old **Schoolagenda** module is gone as a product (read-only from 2022, removed 31 Aug 2025). The website timetable is Planner, not Agenda XML.

### What this MCP calls today (`smartschool_mcp/server.py`)

| MCP tool | Library call | Portal endpoint (approx.) | Matches the website timetable? |
|----------|----------------|---------------------------|--------------------------------|
| `get_schedule` | `PlannedElements` (one day, no `types`) | `GET /planner/api/v1/planned-elements/user/{id}?from=&to=` | **Yes** — same calendar GET as the website |
| `get_future_tasks` | `FutureTasks` | Legacy `/Agenda/Futuretasks/getFuturetasks` | Old Agenda, not Planner |
| `get_planned_elements` | `PlannedElements` | Same path; optional `types` / `includes` (default: omit `types`) | **Yes** — default matches the calendar; pass `types` for sidebar subsets |
| `get_children` | `POST /Studentcard/Student/getStudents` | Mijn kinderen list | Parent/co-account child list |
| `switch_child` | `GET /Studentcard/Chain/gotourl/accountID/{accountId}` | Mijn kinderen switch | After switch, Planner/results follow that child |
| Other tools | Courses, Results, Messages, … | Other portal JSON/HTML routes | Unrelated to the timetable gap |

So: we follow the **website’s portal stack**, not the official developers API. `get_schedule` and `get_planned_elements` both call the Planner calendar GET. `get_future_tasks` is still the old Agenda sidebar.

When changing planner tools: mirror the website query (`from`, `to`, optional `types`, optional `includes`) and pass through `plannedElementType` / `period` fields rather than reshaping them into old Agenda names.

### Portal API inventory

Sanitized website-vs-library-vs-MCP mapping: [`docs/portal-api/`](docs/portal-api/). Playbook, `catalog.yml`, and `gap.md` live there. Do not invent extra MCP abstractions from that catalog until upstream `smartschool` covers the gaps.

## Architecture

Single-file MCP server `smartschool_mcp/server.py` plus `smartschool_mcp/__main__.py`. Top-level `main.py` is a backward-compatibility shim.

### Transport

- **`stdio`** (default, `MCP_TRANSPORT`) — Claude Desktop; `mcp.run()`.
- **`streamable-http`** — remote clients; Starlette via `mcp.streamable_http_app()`, `CORSMiddleware` (so browsers can read `Mcp-Session-Id`), optional `_BearerAuthMiddleware` if `MCP_API_KEY` is set. uvicorn on `MCP_HOST:MCP_PORT`.

### Tools and session

Tools are `@mcp.tool()` functions on `FastMCP("Smartschool MCP")`. `_session()` is lazy (`@lru_cache(maxsize=1)` / OAuth cache); missing credentials fail at tool call, not process start. Tests patch `_session` in `conftest.py` (`autouse`); never hit the network. The opt-in portal smoke (`PORTAL_SMOKE=1`, `pytest -m integration`) is the exception and is excluded from CI.

Library objects are lazy (e.g. `.details` triggers HTTP). Use `getattr(..., default)` where stubs are incomplete. Tools catch `Exception` and return `{"error": ...}` (or a list variant).

Helpers: `_safe_format_date`, `_safe_get_teacher_names`; `_TaskDict` / `_CourseDict` / `_DayDict` for `get_future_tasks`.

### Tests

- `test_helpers.py` — date/teacher helpers
- `test_middleware.py` — bearer auth
- `test_tools.py` — tool error handling and mocked happy paths
- plus `test_auth.py`, `test_main.py`, `test_server_session.py`
- `test_portal_smoke_plan.py` — catalog classifier (no network)
- `test_portal_smoke.py` — live smoke, skipped unless `PORTAL_SMOKE=1`
