# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- `get_schedule` uses the Planner calendar GET (`PlannedElements`, no `types` filter) instead of the removed Schoolagenda XML
- `get_planned_elements` matches that calendar GET by default; optional `types` and `includes` for sidebar subsets
- Pin `smartschool` to `markminnoye/smartschool@517de70` (`planner-calendar-1to1`) until upstream merges Planner calendar
- Bump `smartschool` git pin so XML tools (agenda, messages, …) call `ensure_authenticated()` before the dispatcher POST (empty 200 without a login redirect)

### Added

- `get_children` — list linked children on Mijn kinderen (`POST /Studentcard/Student/getStudents` with XHR headers; topnav gotourl fills the current child's switch id when `accountID` is 0)
- `switch_child(account_id)` — switch the session to another linked child (`GET /Studentcard/Chain/gotourl/accountID/{accountId}`); Planner/results then follow that child. Cross-school hops (De Ring `/otp/...`) are followed without replaying the original host.
- Read-only portal catalog smoke (`scripts/smoke_portal.py`): same `Smartschool` login+cookie session as the MCP, legacy library rows first, then HAR GETs. Writes/auth/unmapped XML POSTs are skipped; CI never runs it (`PORTAL_SMOKE=1` + `pytest -m integration`).
- `get_attachments(message_id)` — list all attachments for a message (name, mime type, size, file ID)
- `download_attachment(message_id, file_id, save_path?)` — download an attachment; defaults to `~/Downloads/smartschool/`, accepts optional `save_path`
- `has_attachments` and `attachment_count` fields in every `get_messages` result

### Fixed

- `switch_child` does not GET another school's `/login` (the library would POST this session's password there and can lock the linked account); it aborts on the redirect to that page
- `download_attachment` calls `session.get()` directly instead of the upstream library's `Attachment.download()`, which incorrectly base64-decodes a raw binary response (upstream bug)
- Homepage HTML parsing falls back to BeautifulSoup when `smartschool.bs4_html` is present but cannot parse the response

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

[Unreleased]: https://github.com/MauroDruwel/Smartschool-MCP/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/MauroDruwel/Smartschool-MCP/compare/v0.1.4...v0.2.0
[0.1.4]: https://github.com/MauroDruwel/Smartschool-MCP/releases/tag/v0.1.4
