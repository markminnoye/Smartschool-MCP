# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- `get_schedule` uses the Planner calendar GET (no `types` filter) instead of the removed Schoolagenda XML. The MCP reads that JSON itself so element id, description, and upload folders are not dropped by `PlannedElement`
- `get_planned_elements` matches that calendar GET by default; optional `types` and `includes` for sidebar subsets
- Pin `smartschool` to `markminnoye/smartschool@517de70` (`planner-calendar-1to1`) until upstream merges Planner calendar
- Bump `smartschool` git pin so XML tools (agenda, messages, …) call `ensure_authenticated()` before the dispatcher POST (empty 200 without a login redirect)

### Added

- Claude Desktop `.mcpb` bundle (`manifest.json`, `scripts/build_mcpb.py`, MCPB workflow artifact). Configure asks for school, username, password, birth date, and child name, and saves them once into `~/.config/smartschool/credentials.json` (Keychain on macOS) when that profile is not already stored. An existing profile wins. With no account, tools return a Dutch Configure message. The bundle vendors `markminnoye/smartschool` at `517de70`.
- Dutch parent test guide: `docs/claude-desktop-test.md`
- `get_children` — list linked children on Mijn kinderen (`POST /Studentcard/Student/getStudents` with XHR headers; topnav gotourl fills the current child's switch id when `accountID` is 0)
- `switch_child(account_id)` — switch the session to another linked child (`GET /Studentcard/Chain/gotourl/accountID/{accountId}`); Planner/results then follow that child. Cross-school hops (De Ring `/otp/...`) are followed without replaying the original host.
- Shared Smartschool account file `~/.config/smartschool/credentials.json` (or `$GROK_PLUGIN_DATA/credentials.json`) for the MCP server, the Claude plugin, and the Grok plugin path. Each profile's `children` list keeps `name` and, when known, the `account_id` and `platform` from `get_children`. Legacy `config.env` and `.env` accounts are copied in once when that file is missing
- Claude Code plugin login guard: one attempt, `auth_failed` blockade, host pin, no mixed env/config credentials
- Claude Code plugin under `claude-plugin/`: local skill and read-only scripts for login, agenda, berichten, and cijfers (one credential file, pinned `smartschool` library, no hosted MCP)
- Read-only portal catalog smoke (`scripts/smoke_portal.py`): same `Smartschool` login+cookie session as the MCP, legacy library rows first, then HAR GETs. Writes/auth/unmapped XML POSTs are skipped; CI never runs it (`PORTAL_SMOKE=1` + `pytest -m integration`).
- `get_attachments(message_id)` — list all attachments for a message (name, mime type, size, file ID)
- `download_attachment(message_id, file_id, save_path?)` — download an attachment; defaults to `~/Downloads/smartschool/`, accepts optional `save_path`
- `has_attachments` and `attachment_count` fields in every `get_messages` result
- `get_schedule` and `get_planned_elements` include planner element `id`, `description`, and `upload_folders`
- `get_planner_attachments` / `download_planner_file` — list and download files attached to a planner item. The website `includes=upload-folders` query is sent; the JSON field names and the file download URL are **not live-verified** (fixtures at `smartschool@517de70` do not contain them). Download uses a same-host URL from that payload when one is present, otherwise GET-only guesses on the same host. `include_raw=true` returns key names and URL-like fields, without cookies or query strings.
- `get_course_documents` / `download_course_document` — course Documenten via `TopNavCourses` + `FolderItem` (`/Documents/Index/Index/...` and `/Documents/Download/Index/...`, both in the library fixtures)

### Removed

- Fly.io / Docker deploy scaffolding (Dockerfile, fly.toml, .dockerignore, docs/deploy-sonicrocket.md) — added prematurely in #3

### Fixed

- `download_course_document` keeps the filename from `Content-Disposition` when the download sends one, and otherwise adds an extension from the short Smartschool type (`pdf`, `docx`) or the real MIME type. An extension that is already present is not doubled.
- `get_results` reads class average and median from the evaluation JSON itself. The library types `centralTendencies` as a list of strings, so object payloads used to fail validation and stay null. Strings, graphics, and fields such as `classAverage` / `gemiddelde` / `mediaan` are accepted. When the school hides the numbers they stay null. `include_raw=true` summarizes the first result's detail keys.
- MCP server login uses the same one-try guard as the Claude plugin: one credential POST, then `~/.cache/smartschool/<subdomain>/<user>/auth_failed`. `/login?error=1` counts as failure. A later call does not send the password again.
- `switch_child` follows the live Mijn kinderen chain from HAR: gotourl → `/otp/{token}` → relative `/Studentcard`. Foreign hops use a raw GET with browser navigation headers so the library cannot POST this account's password on `/login`. Cross-school `account-verification` may still run on a new device; TOTP stays blocked.
- Claude Code plugin: an empty course list or empty body is not a login lockout; `/login?error=1` still writes `auth_failed`; a leading `https://` on `SMARTSCHOOL_MAIN_URL` is stripped; an expired message session exits with an error instead of an empty inbox; grade detail lookups re-raise authentication errors
- Planner week fetch no longer fails when lesson payloads omit `canUserRestoreFromTrash` (`smartschool@517de70`)
- `download_attachment` calls `session.get()` directly instead of the upstream library's `Attachment.download()`, which incorrectly base64-decodes a raw binary response (upstream bug)
- Homepage HTML parsing falls back to BeautifulSoup when `smartschool.bs4_html` is present but cannot parse the response
- `get_results` no longer fails the whole call when `graphic.percentage.color` is `blue` or another value outside the library enum (`green`, `red`, `olive`, `yellow`, `steel`, `grass`). Known colors stay enum members; unknown colors are kept as strings. The fork pin stays at `517de70` because this environment cannot push to `markminnoye/smartschool`.
- `get_future_tasks` falls back to Planner `planned-assignments` (today through the next 366 days) when the legacy Agenda list is empty or unavailable, and keeps the date/course/task shape. De Pass stores Toets and Huistaak on the Planner calendar, not in `/Agenda/Futuretasks/getFuturetasks`.

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
