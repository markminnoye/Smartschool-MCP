# Capture playbook

Repeatable steps to extend [`catalog.yml`](catalog.yml) from a logged-in Chrome session. One school, one role per crawl. This seed used a **parent/co-account** on school A (`school-a.smartschool.be`). Teacher- and admin-only routes will be missing.

## Setup

1. Chrome, logged in as the account you want to inventory (not a separate profile the agent cannot see).
2. DevTools → Network:
   - Filter: **Fetch/XHR** (not JS/CSS/img, unless you are hunting HTML document modules).
   - Enable **Preserve log**.
   - Disable cache while DevTools is open.
3. Keep the HAR off git: save under `docs/portal-api/raw/` (gitignored).

## Per-module pass

Open **every** topnav entry and its submenus once. Minimum for a parent/co-account:

| Nav | Typical actions |
|-----|-----------------|
| Start | Load homepage; open an “in de kijker” block if present |
| Mijn kinderen | Switch child if the UI offers it |
| Vakken | Course list; open one course; open a folder/file if shown |
| Berichten | Inbox list; open one message; if present: search, sent, trash |
| Planner | Week grid (this is the timetable); sidebar to-dos/assignments; pin/unpin if visible |
| Resultaten / rapporten | Evaluations list; one detail; reports tab if visible |
| Documenten / mydocs / intradesk | Root listing; one folder; do **not** upload real student files |
| Zoeken | One search |
| Meldingen | Open the bell/list if present |
| Profiel | Open once (expect auth-ish calls; strip tokens) |

For each screen: list → detail → filter → pagination (if the UI has it).

## Export

1. Network panel → right click → **Save all as HAR with content** (or without content if the UI allows — keys only is enough).
2. Write to `docs/portal-api/raw/<school>-<role>-<module>-<YYYY-MM-DD>.har`.
3. Optional screenshots for orientation also go in `raw/` (they often contain names).

## Extract into the catalog

From the HAR (script or by hand):

1. Unique `METHOD` + path. Ignore static `/smsc/svg/…` unless it is the data channel.
2. Keep query **keys** and JSON/form **keys**. Drop cookies and token values.
3. Replace ids with `{userId}`, `{courseId}`, `{messageId}`, `{fileId}`, `{folderId}`, `{platformId}`, `{revisionId}`, `{evaluationId}`.
4. Collapse `/smsc/locales/{locale}/{bundle}.rev-{hash}.json` to that pattern (one row, not one row per hash).
5. Set `observed_on` to `<host> parent/co-account` (or teacher/admin if that is the session).
6. Fill `library` / `mcp_tool` by matching [`catalog.yml`](catalog.yml) and `smartschool_mcp/server.py`. New prefixes stay `library: unmapped` and `mcp_tool: none`.
7. Update the module status table in [`README.md`](README.md): `not-started` → `captured` → `catalogued`.

## What not to do

- Do not commit HAR, cookies, `getToken` bodies, or response JSON.
- Do not crawl the portal from ad-hoc pytest/curl scripts; capture from the browser.
- Prefer a library class over a raw call when the library covers the route. See [`gap.md`](gap.md).
