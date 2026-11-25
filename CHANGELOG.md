# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Prompts `ochtendbriefing` and `weekbriefing`: a short Dutch morning stand-up and week overview built from the planner (with study material and attachments), timetable, messages, new results and reports. Read-only; also listed in the Claude Desktop extension's `manifest.json`
- Claude Desktop extension: every published release gets a `smartschool-mcp-<version>.mcpb` asset, built by the new `mcpb.yml` workflow next to the PyPI release. `scripts/build_mcpb.py` bundles the package with the repo's own `pyproject.toml` and `uv.lock`; `manifest.json` lists the tools, and a test keeps that list in step with the server
- `get_student_notes(limit?, offset?, category?, since?, include_details?)` — student file notes ("dossierlijnen", Leerlingvolgsysteem); confidential notes are never returned, details only on request. With `include_details`, each note's `details` holds `text` (plain text), `fields`, `date`, `choice` (the label of the note's select field, e.g. the measure of an order measure) and `attachments` (a list of `{name, mime}`)
- `get_student_absences(since?)` — absences recorded in the Leerlingvolgsysteem (`part_of_day` is `null` when not specified)
- Both tools return `{"error": ...}` ("the student file (LVS) is not available for this account") when Smartschool rejects the request, e.g. for an account without the LVS module
- `get_planned_element_details(element_id, element_type?)` — a planner element's description (as text), attachments, links (weblinks, partner and course links, document deeplinks with their course and doc id) and `links_without_url`, the titles linked in the description without an address (usually Bookwidgets)
- `download_planned_attachment(element_id, attachment_id, element_type?, save_path?)`
- `get_course_documents(course?, folder_id?)` and `download_course_document(course, doc_id, folder_id?, save_path?)` — a course's Documenten; `course` is an id or part of the name, and leaving it out lists the courses
- `get_learning_paths(course?)` and `get_learning_path(course, learning_path_id, resolve_links?)` — a course's leerpaden, and a leerpad's sections and steps with `done`, `progress_percent` and, for web links and external exercises (Bookwidgets), their address. Only the overview is read, so the pupil's progress is left untouched
- `get_planned_elements`: each element has `id`, `platform_id`, `type_label`, `is_exam`, and `has_attachments`, `has_links` (links with an address, or Bookwidget titles without one) and `has_description` (a description that says more than the name); new `types` and `include_details` parameters. The planner list is asked with `includes`, so this content comes with the one request, details included

### Fixed

- `get_schedule` and `get_future_tasks` read the planner when the legacy agenda is empty, as it is at schools that moved to the planner: the timetable comes from the `planned-lessons`, `planned-placeholders` and `planned-lesson-cluster-moments` elements, the tasks from `planned-assignments` and `planned-to-dos`. Both results carry `source` (`"agenda"` or `"planner"`); when the planner can't be read, the agenda's result comes back with `planner_error`

### Changed

- `get_planned_elements` returns every planner type that matters for planning: besides assignments and to-dos, events (exam periods, report days, parent evenings), days off and school activities, and the lessons that are an exam or a test. Plain lessons and empty timetable slots are left out unless asked for with `types`, and a lesson that duplicates an assignment of the same name on the same day is dropped
- `download_attachment` and `download_homepage_image` share the new downloads' file-saving code (same behaviour)
- Requires `smartschool>=0.13.0` (planner `element_types`, and `authenticated_user` for parent co-accounts); `uv.lock` now resolves it from PyPI instead of a git checkout (the `[tool.uv.sources]` entry is gone)
- Parse homepage HTML with BeautifulSoup directly, since `smartschool` 0.12 no longer exports `bs4_html`

## [0.3.0] - 2026-09-22

### Added

- `get_attachments(message_id)` — list all attachments for a message (name, mime type, size, file ID)
- `download_attachment(message_id, file_id, save_path?)` — download an attachment; defaults to `~/Downloads/smartschool/`, accepts optional `save_path`
- `has_attachments` and `attachment_count` fields in every `get_messages` result
- Mauro Quality Gate (MQG) certification: `.editorconfig`, strict audit workflow check in CI, and repo topic refinement

### Fixed

- `download_attachment` calls `session.get()` directly instead of the upstream library's `Attachment.download()`, which incorrectly base64-decodes a raw binary response (upstream bug)
- Expired sessions no longer make XML-backed tools (`get_messages`, `get_attachments`, `get_future_tasks`) report empty results instead of an error. The environment-credential session is now TTL-cached like the OAuth one, and every session is liveness-probed once with a `GET /` so a dead cookie triggers a real re-login

### Changed

- Upgraded `mcp` SDK to 2.x (`>=2.1.1,<3`), migrating `FastMCP` to `MCPServer`, updating HTTP transport configurations (`stateless_http`, `json_response`, `transport_security`) to `streamable_http_app()`, and typing `SmartschoolOAuthProvider` with `OAuthAuthorizationServerProvider`. `FastMCP` alias is preserved for backwards compatibility
- Updated dependencies: `smartschool>=0.10.0`, `cachetools>=7.1.8`, `types-cachetools>=7.0.0.20260713`, dev tools (`pytest>=9.1.1`, `pytest-cov>=7.1.0`), and GitHub actions (`actions/checkout@v7`, `peter-evans/create-pull-request@v8`)

## [0.2.0] - 2026-03-25

### Added

- **Remote MCP support** via Streamable HTTP transport (`--transport streamable-http`)
- `--host`, `--port` CLI flags with `MCP_HOST`, `MCP_PORT`, `MCP_TRANSPORT` env var counterparts
- Optional Bearer-token authentication via `MCP_API_KEY` (`_BearerAuthMiddleware`)
- CORS middleware — required for browser-based clients such as claude.ai
- `get_schedule(date_offset)` — daily lesson schedule via `SmartschoolLessons`
- `get_periods()` — academic terms/periods via `Periods`
- `get_reports()` — report cards via `Reports`
- `get_planned_elements(days_ahead)` — planner items via `PlannedElements`
- `get_student_support_links()` — school support resources via `StudentSupportLinks`
- `achieved_points`, `total_points`, `percentage` fields in `get_results`
- `teacher` (from `gradebook_owner`, no extra API call) and `period` in `get_results`
- `warning` field in `get_future_tasks` tasks
- Lazy `_session()` singleton — session is created on first tool invocation, not at import time
- Professional OSS infrastructure: CI workflow, issue/PR templates, CodeRabbitAI, pre-commit, tests

### Changed

- Updated `smartschool` dependency from personal fork (v0.5.0) to official library (`svaningelgem/smartschool` v0.8.0+)
- Relaxed Python requirement from `>=3.13` to `>=3.10`
- Modernized all type hints: replaced `typing.List/Dict/Optional` with built-in generics (`list`, `dict`, `str | None`)
- `get_results`: teacher now read from `result.gradebook_owner` (always available); details fetch only used for central tendencies
- `get_messages`: sender filter applied directly from headers (no full message fetch needed); body fetched lazily
- `get_future_tasks`: fixed `course.course_title` (was incorrectly `course.name`)
- Fixed `total_tasks` calculation (was counting dict keys, not tasks)
- Fixed `result.availability_date` and `result.does_count` attribute names (camelCase → snake_case)
- Fixed `teacher.name.starting_with_last_name` / `starting_with_first_name` attribute names
- Fixed `header.unread` (was `header.read`)
- Replaced removed `ResultDetail` class with lazy-loaded `result.details` property
- Updated `publish.yml` to use `uv build` instead of legacy `python -m build` + pip

### Removed

- `ResultDetail` import (class removed in official smartschool library)

## [0.1.4] - 2026-03-01

### Added

- Initial release with `get_courses`, `get_results`, `get_future_tasks`, `get_messages` tools
- Claude Desktop integration via stdio transport
- PyPI distribution and MCP Registry listing

[Unreleased]: https://github.com/MauroDruwel/Smartschool-MCP/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/MauroDruwel/Smartschool-MCP/compare/v0.1.4...v0.3.0
[0.2.0]: https://github.com/MauroDruwel/Smartschool-MCP/compare/v0.1.4...v0.2.0
[0.1.4]: https://github.com/MauroDruwel/Smartschool-MCP/releases/tag/v0.1.4
