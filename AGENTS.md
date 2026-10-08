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
| `get_schedule` | calendar JSON (same query as `PlannedElements`, one day, no `types`) | `GET /planner/api/v1/planned-elements/user/{id}?from=&to=` | **Yes** — same calendar GET as the website. Also returns `id`, `description`, and `upload_folders` when the JSON has them |
| `get_future_tasks` | `FutureTasks`, then `PlannedElements` (`types=planned-assignments`) when that list is empty | Legacy `/Agenda/Futuretasks/getFuturetasks`, else Planner calendar | Planner fallback on schools where Agenda is empty |
| `get_planned_elements` | calendar JSON (same query as `PlannedElements`) | Same path; optional `types` / `includes` (default: omit `types`) | **Yes** — default matches the calendar; pass `types` for sidebar subsets |
| `get_planner_attachments` / `download_planner_file` | calendar JSON with `includes=icon,courses,locations,upload-folders,labels`; assignment detail only as fallback | `GET /planner/api/v1/planned-elements/user/{id}` and, if needed, `GET /planner/api/v1/planned-assignments/{platformId}/{assignmentId}` | Include list matches the website. **Response body for upload folders and assignment detail is not live-verified.** Download uses a URL from that payload only |
| `get_course_documents` / `download_course_document` | `TopNavCourses` + `FolderItem` | `GET /Documents/Index/Index/courseID/{courseId}/ssID/{platformId}` and `GET /Documents/Download/Index/...` | Documenten in the lesson module. Not planner upload folders |
| `get_children` | `POST /Studentcard/Student/getStudents` (XHR) + Studentcard topnav | Mijn kinderen list | Parent/co-account child list |
| `switch_child` | `GET /Studentcard/Chain/gotourl/accountID/{accountId}` | Mijn kinderen switch | After switch, Planner/results follow that child |
| Other tools | Courses, Results, Messages, … | Other portal JSON/HTML routes | Unrelated to the timetable gap |

So: we follow the **website’s portal stack**, not the official developers API. `get_schedule` and `get_planned_elements` both call the Planner calendar GET. `get_future_tasks` still tries the old Agenda sidebar first, and uses Planner `planned-assignments` when that list is empty.

When changing planner tools: mirror the website query (`from`, `to`, optional `types`, optional `includes`) and pass through `plannedElementType` / `period` fields rather than reshaping them into old Agenda names.

### Portal API inventory

Sanitized website-vs-library-vs-MCP mapping: [`docs/portal-api/`](docs/portal-api/). Playbook, `catalog.yml`, and `gap.md` live there. Do not invent extra MCP abstractions from that catalog until upstream `smartschool` covers the gaps.

## Architecture

MCP server `smartschool_mcp/server.py` plus `smartschool_mcp/planner_fields.py` (calendar id, description, upload folders) and `smartschool_mcp/__main__.py`. Top-level `main.py` is a backward-compatibility shim.

### Transport

- **`stdio`** (default, `MCP_TRANSPORT`) — Claude Desktop; `mcp.run()`.
- **`streamable-http`** — remote clients; Starlette via `mcp.streamable_http_app()`, `CORSMiddleware` (so browsers can read `Mcp-Session-Id`), optional `_BearerAuthMiddleware` if `MCP_API_KEY` is set. uvicorn on `MCP_HOST:MCP_PORT`.

### Tools and session

Tools are `@mcp.tool()` functions on `FastMCP("Smartschool MCP")`. `_session()` is lazy (`@lru_cache(maxsize=1)` / OAuth cache); missing credentials fail at tool call, not process start. Tests patch `_session` in `conftest.py` (`autouse`); never hit the network. The opt-in portal smoke (`PORTAL_SMOKE=1`, `pytest -m integration`) is the exception and is excluded from CI.

Stdio and OAuth sessions are `GuardedSession` (`smartschool_mcp/guard.py`): one credential POST, then `~/.cache/smartschool/<subdomain>/<user>/auth_failed` (the same cache as the shared credential store). `/login?error=1` is a failure. `SMARTSCHOOL_MAIN_URL` accepts a bare school subdomain (`dering` → `dering.smartschool.be`). Single-user mode calls `prepare_server_credentials()` before opening that session. A complete Configure dialog is saved once when that profile is not already stored; an existing profile wins. With no account at all, tools return a Dutch Configure message instead of the library attribute error. The child name on the saved profile is read into the server instructions for `switch_child`.

Claude Desktop install: `manifest.json` (mcpb `uv`) plus `scripts/build_mcpb.py`. Optional `user_config` fields (school, username, password, birth date, child name) map to `SMARTSCHOOL_*` and are saved into `~/.config/smartschool/credentials.json` when that profile is missing. The MCPB workflow uploads `dist/smartschool-mcp.mcpb`. The bundle vendors the pinned fork so the parent install does not clone git. Parent steps: `docs/claude-desktop-test.md`.

Each new test-build `.mcpb` gets the next version so Claude Desktop shows Update. `manifest.json` uses `0.3.0-rc.N`. This test artifact is `0.3.0-rc.1`; the next one is `0.3.0-rc.2`. `scripts/build_mcpb.py` uses the PEP 440 form `0.3.0rcN` (`uv lock` rejects `0.3.0-rc.N`). Release PRs stay on `0.2.0` unless a version bump is already part of them. Name the manifest version in `docs/claude-desktop-test.md`.

Library objects are lazy (e.g. `.details` triggers HTTP). Use `getattr(..., default)` where stubs are incomplete. Tools catch `Exception` and return `{"error": ...}` (or a list variant).

Helpers: `_safe_format_date`, `_safe_get_teacher_names`; `_TaskDict` / `_CourseDict` / `_DayDict` for `get_future_tasks`.

### Tests

- `test_helpers.py` — date/teacher helpers
- `test_middleware.py` — bearer auth
- `test_tools.py` — tool error handling and mocked happy paths
- plus `test_auth.py`, `test_main.py`, `test_server_session.py`, `test_login_guard.py`, `test_mcpb_manifest.py`
- `test_portal_smoke_plan.py` — catalog classifier (no network)
- `test_portal_smoke.py` — live smoke, skipped unless `PORTAL_SMOKE=1`
