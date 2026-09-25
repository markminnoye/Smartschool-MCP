# Smartschool Claude Code plugin

Local Claude Code plugin for **one** Smartschool account: agenda (planner), berichten, and cijfers. It is a skill plus thin Python scripts that call the `smartschool` library already pinned in this repository. There is no hosted MCP server in this folder.

The MCP server at the repo root is separate. A hosted demo is out of scope.

## Layout

```text
claude-plugin/
  .claude-plugin/plugin.json    # manifest (only file inside .claude-plugin/)
  skills/smartschool/SKILL.md   # when to run which script
  scripts/                      # login, schedule, messages, results, courses
  config.example.env            # credential template, no secrets
```

## Install

From a checkout of this repo (the scripts need the pinned library in `uv.lock`):

```bash
uv sync
claude --plugin-dir ./claude-plugin
```

Zip install: pack this directory, not the whole repo, and leave secrets out.

```bash
zip -r smartschool-claude-plugin.zip claude-plugin \
  -x 'claude-plugin/config.env' '*/__pycache__/*'
```

Unzip somewhere, then point Claude Code at the folder that contains `.claude-plugin/`:

```bash
claude --plugin-dir /path/to/claude-plugin
```

The skill tells Claude to run scripts with `uv run --project <repo>`. Keep the plugin path inside this checkout (`./claude-plugin`) so that project path resolves. If you unzip the plugin on its own, run the scripts with this repo's environment instead of installing a second copy of `smartschool`:

```bash
uv run --project /path/to/Smartschool-MCP \
  python /path/to/claude-plugin/scripts/login.py
```

In Claude Code the skill is `/smartschool:smartschool`. Claude also loads it when you ask about an agenda, berichten, or cijfers.

## Credentials

One account. Copy the example and edit it locally:

```bash
cp claude-plugin/config.example.env claude-plugin/config.env
```

| Variable | Meaning |
| --- | --- |
| `SMARTSCHOOL_MAIN_URL` | School host, e.g. `school.smartschool.be` (no `https://`) |
| `SMARTSCHOOL_USERNAME` | Username |
| `SMARTSCHOOL_PASSWORD` | Password |
| `SMARTSCHOOL_MFA` | Birth date `YYYY-MM-DD`, or a Google Authenticator secret |

`config.env` is gitignored. `SMARTSCHOOL_CONFIG=/path/to/file` selects a different file.

Credential rule: one source only. If `config.env` and the environment both set any of the four `SMARTSCHOOL_*` variables and the values are not identical, every script stops before login. That avoids mixing a parent password from the shell with a child username from the file.

A failed login writes `~/.cache/smartschool/<user>/auth_failed`. Later runs refuse until that file is deleted by hand. Scripts do not retry and must not be started in parallel.

Do not commit real values. Do not put secrets in `plugin.json` or `SKILL.md`.

## Try the scripts

From the repository root, after `uv sync` and a filled-in `config.env`:

```bash
uv run python claude-plugin/scripts/login.py
uv run python claude-plugin/scripts/schedule.py
uv run python claude-plugin/scripts/schedule.py --offset 1
uv run python claude-plugin/scripts/schedule.py --days-ahead 6
uv run python claude-plugin/scripts/messages.py --limit 10
uv run python claude-plugin/scripts/results.py --limit 10 --no-details
uv run python claude-plugin/scripts/courses.py
```

Each script prints JSON. `"error"` means the call failed (missing config, login, or portal). They only read data.

| Script | MCP tool it mirrors | What you get |
| --- | --- | --- |
| `login.py` | session login | School host and a few user fields |
| `schedule.py` | `get_schedule`, `get_planned_elements` | Planner calendar (`from`/`to`, optional `types`) |
| `messages.py` | `get_messages` | Inbox or another box; `--id` reads one message |
| `results.py` | `get_results` | Grades, optional course filter |
| `courses.py` | `get_courses` | Course names and teachers |

`schedule.py` uses `PlannedElements` (the website timetable). It does not call the removed Schoolagenda XML API.
