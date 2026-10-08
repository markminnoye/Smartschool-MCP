---
name: smartschool
description: >
  Read Smartschool agenda (planner), berichten (messages), and cijfers
  (grades) for one or more saved school logins. Use when the user asks about
  their timetable, lessons, school inbox, results, or whether Smartschool
  login works. Load saved profiles and run the local Python scripts.
  Read-only; no hosted MCP.
---

# Smartschool (read-only profiles)

Use this skill for the user's own Smartschool data: agenda, berichten, and cijfers.

Do not start a web server and do not add `.mcp.json` / `mcpServers`. Do not compose, delete, or move messages.

Each saved profile is one school login. Setup stores the child's name on that profile so you know which login belongs to which child. Show that name when you answer. The `children` list holds those names and, when known, the same `account_id` and `platform` that `get_children` returns. These scripts do not switch the child. Mijn kinderen switching is the MCP tools `get_children` and `switch_child`. `switch_child` follows a cross-school `/otp/` hop and does not post this account's password on the other school's login page. Separate school logins stay separate profiles.

## Configure

The account file is shared with the MCP server and any Grok plugin: `credentials.json` in `$GROK_PLUGIN_DATA` when that variable is set, otherwise `~/.config/smartschool/credentials.json`. `config.example.env` only names that file. Do not create a second account in `config.env` or in a repo `.env`.

1. Run `login.py` in a terminal once, or reuse a profile the MCP server already saved.
2. Never commit the credentials file or paste the password or birth date into chat.

| Variable | Meaning |
| --- | --- |
| `SMARTSCHOOL_MAIN_URL` | School subdomain or host, e.g. `dering` or `dering.smartschool.be`. A leading `https://` is removed. |
| `SMARTSCHOOL_USERNAME` | Smartschool username |
| `SMARTSCHOOL_PASSWORD` | Password |
| `SMARTSCHOOL_MFA` | Birth date of the child, `YYYY-MM-DD` |

A complete environment overrides one run and is not saved. If that environment and a legacy file disagree, the script stops and does not log in. The library requires all four values and posts the birth date unchanged. `SMARTSCHOOL_MAIN_URL` must be https and end in `.smartschool.be` after normalization. `SMARTSCHOOL_USER` is an alias of `SMARTSCHOOL_USERNAME`.

Lookup order:

1. `SMARTSCHOOL_USER` (or `SMARTSCHOOL_USERNAME`) and `SMARTSCHOOL_PASSWORD` in the environment. `SMARTSCHOOL_CHILD` can name the child for that env-only account. Host and birth date come from the environment, then from the matching saved profile.
2. `credentials.json` in `$GROK_PLUGIN_DATA` when that variable is set, otherwise `~/.config/smartschool/credentials.json`.
3. One legacy seed (`SMARTSCHOOL_CONFIG`, `config.env`, or `.env`) only when the shared file is still missing. After that, use the shared file.

If nothing is found and the script has a terminal, it asks once: school subdomain, username, password, geboortedatum (`jjjj-mm-dd`), and the child's name. It stores the profile (directory mode 700, file mode 600) and does not log in during the prompt. On macOS it can store the password and birth date in the Keychain via `security`; the file then has the username, host, and child names. If nothing is found and there is no terminal, stdout is a JSON error containing `Geen loginpoging gedaan`. Do not run the script again and do not try to log in another way.

Choose one profile with `--profile` or `SMARTSCHOOL_PROFILE` (`dering` when unique, `dering:user`, or `user@dering`). Omit it to return every saved profile under `profiles`. Each object includes `profile`, `child`, and `children`.

Do not print, quote, or invent the password or birth date. Do not read `credentials.json` back into the chat.

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

Stdout is JSON. If the object contains `"error"`, or any item under `profiles` contains `"error"`, report that message and stop for that profile.

A message that contains `LOGIN FAILED, niet opnieuw proberen` means a password POST already failed. Stop. Do not run this script or another script again. The user must delete the `auth_failed` file named in the message, or ask to wipe that profile. Do not delete that file yourself.

Any other error (missing credentials, a bad flag, a host that is not a Smartschool school) happens before a password POST. Tell the user. After they fix the saved profile or the command, you may run that same script once more. Do not run scripts in parallel. Do not add extra POSTs.

`login.py --reset` deletes one profile, its Keychain items, the session cache, and the `auth_failed` marker. Pass `--profile` (or set `SMARTSCHOOL_PROFILE`) when more than one profile is saved. Without a selector the script refuses and deletes nothing (`Niets gewist`). On a terminal it then asks for a replacement profile. It does not log in. Pass `--reset` only when the user asks to wipe or replace a saved profile. A JSON error that contains `Geen loginpoging gedaan` means no login was attempted; stop and tell the user what is missing.

## Commands

Prefix every command with:

```bash
uv run --project "${CLAUDE_PLUGIN_ROOT}/.." python "${CLAUDE_PLUGIN_ROOT}/scripts/<name>.py"
```

Add `--profile dering` (or `dering:user`) when the user named one child or one school. Omit `--profile` when they want every saved login.

Agenda (full calendar for one day; same portal call as the website, no `types` filter):

```bash
schedule.py
schedule.py --profile dering
schedule.py --offset 1
schedule.py --from 2026-09-22 --days-ahead 6
schedule.py --types planned-lessons
```

`--offset 0` is today. `--types` is optional (`planned-lessons`, `planned-assignments`, `planned-to-dos`, …). Omit it for the full timetable. `--includes` is forwarded (the website uses `icon,courses,locations,upload-folders,labels`). Whether that include adds upload-folder JSON is not live-verified.

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

Reply in the user's language (Dutch questions get a Dutch answer). Turn the JSON into a short list: child name, time, subject, room, sender, or score. When several profiles come back, keep each child's block separate and name the child. Do not dump the raw JSON unless they ask. Do not include the password, birth date, or `config.env` contents.
