# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.0] - 2026-11-23

First public release of **Smartschool voor ouders**, the Claude Desktop extension. Earlier 0.x test releases were withdrawn. It builds on the original Smartschool MCP by Mauro Druwel and the [`svaningelgem/smartschool`](https://github.com/svaningelgem/smartschool) portal client (both MIT).

### Distribution

- One `.mcpb` per GitHub Release (`smartschool-mcp-<version>.mcpb`), built by `mcpb.yml`. Nothing is published to PyPI or the MCP Registry. The bundle vendors the pinned `markminnoye/smartschool` fork.

### What it can do (read-only)

- Planner timetable and tasks: `get_schedule`, `get_planned_elements`, `get_future_tasks`, plus planner attachments and downloads.
- Results with teacher feedback and, when the school shares them, class average and median: `get_results`.
- School messages with search and attachments: `get_messages`, `get_attachments`, `download_attachment`.
- Course documents (Documenten), periods, report cards, support links and the homepage "in de kijker" blocks.
- Several children on one parent account: `get_children` and `switch_child`, including children at another school.
- Dutch prompts **ochtendbriefing** (daily standup) and **weekbriefing** (coming school week) in Claude Desktop.
- Nothing is sent, answered or changed in Smartschool.

### Setup and privacy

- Claude Desktop's Configure dialog asks once for school, username, password, the child's birth date and name, and saves them in `~/.config/smartschool/credentials.json`; on macOS the password and birth date go to the Keychain.
- One password attempt: a rejected login writes `auth_failed` and is never retried. A network error does not lock.
- Downloads only land in `~/Downloads/smartschool` (or `SMARTSCHOOL_DOWNLOAD_DIR`) and its subfolders, never overwrite a file, and refuse a Smartschool login page.

### Also included

- Claude Code plugin under `claude-plugin/` with a Dutch `daily-briefing` skill and read-only scripts.
- Optional Streamable HTTP transport: binds `127.0.0.1` by default, needs `MCP_API_KEY` on any other address; universal OAuth mode with an in-memory 15-minute lockout per username.

[Unreleased]: https://github.com/markminnoye/Smartschool-MCP/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/markminnoye/Smartschool-MCP/releases/tag/v1.0.0
