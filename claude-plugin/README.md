# Smartschool Claude Code plugin

Local Claude Code plugin for Smartschool: agenda (planner), berichten, cijfers, and a Dutch **ochtendbriefing** for parents. It is skills plus thin Python scripts that call the `smartschool` library already pinned in this repository. There is no hosted MCP server in this folder. Several school logins can be stored as profiles. Each profile is one login and can carry a list of child names.

The MCP server at the repo root is separate. A hosted demo is out of scope.

## Layout

```text
claude-plugin/
  .claude-plugin/plugin.json         # manifest (only file inside .claude-plugin/)
  skills/smartschool/SKILL.md        # when to run which script
  skills/daily-briefing/SKILL.md     # Dutch weekday parent briefing
  scripts/                           # login, schedule, messages, results, courses
  config.example.env                 # credential template, no secrets
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

In Claude Code the skills are `/smartschool:smartschool` (which script to run) and `/smartschool:daily-briefing` (short Dutch parent briefing for one child). Claude also loads them when you ask about an agenda, berichten, cijfers, or an ochtendrapport. The briefing prefers Smartschool MCP tools when they are connected; otherwise it uses these scripts. Mail delivery is optional and only when you ask and a mail tool is available.

## Credentials

One shared file for the Claude plugin, the Grok plugin, and the MCP server. `config.example.env` only points at it. Do not keep a second account in `config.env`.

| Variable | Meaning |
| --- | --- |
| `SMARTSCHOOL_MAIN_URL` | School subdomain or host, e.g. `dering` or `dering.smartschool.be` (no `https://`). A full `https://` URL is accepted and normalized to `<label>.smartschool.be`. |
| `SMARTSCHOOL_USERNAME` | Username |
| `SMARTSCHOOL_PASSWORD` | Password |
| `SMARTSCHOOL_MFA` | Birth date of the child, `YYYY-MM-DD`. The library posts this unchanged as `security_question_answer`. |

`SMARTSCHOOL_USER` is an alias of `SMARTSCHOOL_USERNAME`; if both are set they must match. `SMARTSCHOOL_CHILD` names the child when the account comes only from the environment. If the environment and a legacy file both set the four account variables and the values differ, loading that file stops before login.

## Saved credentials

A profile is one school login (`subdomain` + username). Each profile stores a `children` list. Setup asks for one `{name}`. When a child is already known from `get_children`, the same record also keeps `account_id` and `platform` so `switch_child` can use that id. These scripts do not call Mijn kinderen. The MCP tools `get_children` and `switch_child` do; `switch_child` follows a cross-school `/otp/` hop and does not post this account's password on the other school's login.

Lookup order:

1. `SMARTSCHOOL_USER` (or `SMARTSCHOOL_USERNAME`) and `SMARTSCHOOL_PASSWORD` already in the environment. That pair is one account for this process and is not written into the file. `SMARTSCHOOL_MAIN_URL` and `SMARTSCHOOL_MFA` come from that environment, then from the matching saved profile.
2. `credentials.json` in `smartschool_mcp.credentials`. When `GROK_PLUGIN_DATA` is set, the file is `$GROK_PLUGIN_DATA/credentials.json`. Otherwise it is `~/.config/smartschool/credentials.json`. Shape: `{"profiles":[{"main_url","username","children":[{"name","account_id?","platform?"}], ...}]}`. An older single-object file still loads.
3. If that file does not exist yet, one legacy account is copied into it from `SMARTSCHOOL_CONFIG`, `claude-plugin/config.env`, or `./.env`. Two different legacy accounts stop the copy. After the copy, the shared file is the account. Delete the passwords from the old files.

The directory is mode `700` and the file is mode `600` (owner only). A directory needs the execute bit, so it is `700` rather than `600`.

On macOS, when `security` is on `PATH`, the password and birth date go to the Keychain (services `smartschool-mcp` and `smartschool-mcp-mfa`, account `username@host`). The file then contains `username`, `main_url`, and `children` only. Set `SMARTSCHOOL_KEYCHAIN=0` to keep the secrets in the file instead.

Choose a profile with `--profile` or `SMARTSCHOOL_PROFILE`: a subdomain (`dering`) when it is unique, `subdomain:username`, or `user@subdomain`. Omit the selector and every saved profile is used. JSON for one profile stays flat and adds `profile`, `child` (first stored name), and `children` (name list). Several profiles come back as `{"profiles":[...]}`.

The first run with no account and a terminal asks for the school subdomain, username, password, geboortedatum (`jjjj-mm-dd`), and the child's name, then stores them. It does not print the password or the birth date, and it does not log in during that prompt. With no terminal, the script prints a JSON error that says `Geen loginpoging gedaan` and does not contact Smartschool.

A successful portal call writes `~/.cache/smartschool/<subdomain>/<user>/session.json` with an `expires_at` time (default 8 hours, override with `SMARTSCHOOL_SESSION_TTL` in seconds). When that time has passed, or when the cookie cache has no expiry yet, the cookie file is deleted before the next login. `session.json` holds no password.

Wipe one profile (file entry, Keychain items, session cache, and `auth_failed`) without logging in:

```bash
uv run python claude-plugin/scripts/login.py --reset --profile dering
```

With several profiles and no `--profile`, `--reset` deletes nothing and returns a JSON error (`Niets gewist`). On a terminal, a reset that matches one profile asks for a replacement and stores it. It still does not log in. Passwords, birth dates, and file contents never appear in logs, JSON, or error text.

A failed login writes `~/.cache/smartschool/<subdomain>/<user>/auth_failed`. Later runs for that profile refuse until that file is deleted by hand. Scripts do not retry and must not be started in parallel. A failed profile in a multi-profile run is reported on that profile only; the others are not retried.

Do not commit real values. Do not put secrets in `plugin.json` or `SKILL.md`.

## Try the scripts

From the repository root, after `uv sync` and a saved profile:

```bash
uv run python claude-plugin/scripts/login.py
uv run python claude-plugin/scripts/schedule.py
uv run python claude-plugin/scripts/schedule.py --offset 1
uv run python claude-plugin/scripts/schedule.py --days-ahead 6
uv run python claude-plugin/scripts/messages.py --limit 10
uv run python claude-plugin/scripts/results.py --limit 10 --no-details
uv run python claude-plugin/scripts/courses.py
```

Each script prints JSON. `"error"` means the call failed (missing config, login, or portal). They only read data. `child` is the name stored for that login. Switching the selected child on Mijn kinderen is the MCP tool `switch_child`, not these scripts.

| Script | MCP tool it mirrors | What you get |
| --- | --- | --- |
| `login.py` | session login | School host and a few user fields |
| `schedule.py` | `get_schedule`, `get_planned_elements` | Planner calendar (`from`/`to`, optional `types`) |
| `messages.py` | `get_messages` | Inbox or another box; `--id` reads one message |
| `results.py` | `get_results` | Grades, optional course filter |
| `courses.py` | `get_courses` | Course names and teachers |

`schedule.py` uses the same raw Planner calendar GET as `get_schedule`. It does not call the removed Schoolagenda XML API.
