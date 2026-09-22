---
name: smartschool
description: >
  Read one Smartschool account: agenda (planner), berichten (messages), and
  cijfers (grades). Use when the user asks about their timetable, lessons,
  school inbox, results, or whether Smartschool login works. Load a single
  credential file and run the local Python scripts. Read-only; no hosted MCP.
---

# Smartschool (one account, read-only)

Use this skill for the user's own Smartschool data: agenda, berichten, and cijfers.

Do not start a web server, do not add `.mcp.json` / `mcpServers`, and do not switch accounts. One credential set only. Do not compose, delete, or move messages.

## Configure

1. Copy `${CLAUDE_PLUGIN_ROOT}/config.example.env` to `${CLAUDE_PLUGIN_ROOT}/config.env`.
2. Fill in one account. Never commit `config.env` or paste the password into chat.

| Variable | Meaning |
| --- | --- |
| `SMARTSCHOOL_MAIN_URL` | School host only, e.g. `school.smartschool.be` (no `https://`) |
| `SMARTSCHOOL_USERNAME` | Smartschool username |
| `SMARTSCHOOL_PASSWORD` | Password |
| `SMARTSCHOOL_MFA` | Birth date `YYYY-MM-DD`, or the Google Authenticator secret |

Already-exported environment variables win over the file. The `smartschool` library requires all four values.

Scripts import that library. Use the pin already in this repo (`pyproject.toml` / `uv.lock`, git rev `517de70`). Do not add a second dependency.

When this plugin directory lives inside the Smartschool-MCP checkout:

```bash
uv run --project "${CLAUDE_PLUGIN_ROOT}/.." python "${CLAUDE_PLUGIN_ROOT}/scripts/login.py"
```

If that project path has no `pyproject.toml`, stop and follow `claude-plugin/README.md`. Do not invent another install of `smartschool`.

## Which script

| User asks about | Script |
| --- | --- |
| Login, "kan ik inloggen" | `scripts/login.py` |
| Agenda, lessons, planner, today, this week | `scripts/schedule.py` |
| Berichten, inbox, messages | `scripts/messages.py` |
| Cijfers, grades, points, results | `scripts/results.py` |
| Courses, vakken, teachers | `scripts/courses.py` |

Stdout is JSON. If the object contains `"error"`, report that message and stop. Do not retry with a different account.

Run `login.py` first when another script fails with an authentication error.

## Commands

Prefix every command with:

```bash
uv run --project "${CLAUDE_PLUGIN_ROOT}/.." python "${CLAUDE_PLUGIN_ROOT}/scripts/<name>.py"
```

Agenda (full calendar for one day; same portal call as the website, no `types` filter):

```bash
schedule.py
schedule.py --offset 1
schedule.py --from 2026-09-22 --days-ahead 6
schedule.py --types planned-lessons
```

`--offset 0` is today. `--types` is optional (`planned-lessons`, `planned-assignments`, `planned-to-dos`, …). Omit it for the full timetable.

Berichten (default inbox, headers only):

```bash
messages.py --limit 15
messages.py --box SENT --sender "Jansen"
messages.py --search huiswerk --body
messages.py --id 123
```

`--box` is `INBOX`, `SENT`, `DRAFT`, `SCHEDULED`, or `TRASH`.

Cijfers:

```bash
results.py --limit 15
results.py --course wiskunde --no-details
```

`--no-details` skips the extra average/median request per grade.

Courses:

```bash
courses.py
```

## Answer the user

Reply in the user's language (Dutch questions get a Dutch answer). Turn the JSON into a short list: time, subject, room, sender, or score. Do not dump the raw JSON unless they ask. Do not include the password, birth date, or `config.env` contents.
