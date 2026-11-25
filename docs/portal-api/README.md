# Portal API inventory

This tree maps the **logged-in Smartschool website** (portal REST / XML), not the official partner API and not SOAP `/Webservices/V3`.

It is a reference for adding tools: what the website calls, what [`svaningelgem/smartschool`](https://github.com/svaningelgem/smartschool) wraps, and which MCP tool uses it. Start at [`playbook.md`](playbook.md), then [`catalog.yml`](catalog.yml), then [`gap.md`](gap.md). School hostnames are anonymised as `school-a` / `school-b`.

## How complete is this crawl?

Three parent/co-account HAR exports from 2026-09-17 share the gitignored path `docs/portal-api/raw/school-b.smartschool.be.har` (the file was overwritten each time). The first capture covered **school A** (`school-a.smartschool.be`) and **school B** (`school-b.smartschool.be`). The second is school B–only Intradesk, Zoeken, and Vakken course folders. The third is school B **Meldingen** (`/Profile/Notify`) plus a click-through into a message. Nav modules that appear in those HARs are `catalogued` below. Library-covered modules that were **not** opened stay catalogued from the installed `smartschool` package only.

Teacher-only and admin-only routes are out of scope for this account.

## How to crawl

Follow [`playbook.md`](playbook.md). Export HAR to `docs/portal-api/raw/` (gitignored). Promote unique `METHOD` + path + query/body **keys** into `catalog.yml`. Never commit cookies, tokens, names, or response bodies.

## How to sanitize

1. Drop `Cookie`, `Set-Cookie`, `Authorization`, CSRF tokens, `getToken` values.
2. Keep query/body **key names**; drop values that are dates tied to a person, names, or ids.
3. Replace ids with placeholders: `{userId}`, `{courseId}`, `{messageId}`, `{fileId}`, `{folderId}`, `{platformId}`, `{revisionId}`, `{evaluationId}`, `{accountId}`, `{formId}`, `{formRoundId}`, `{entryId}`, `{assignmentId}`.
4. Collapse cache-busted static files (`*.rev-{hash}.json`) to a path pattern.
5. Skip `/smsc/svg/…` and other static assets unless they are the only way a module loads data.
6. For XML dispatchers, keep `subsystem`/`action` names only — never the rest of the command XML.

## Catalog schema

Each `catalog.yml` record:

| Field | Notes |
|--------|--------|
| `method` | HTTP method |
| `path` | Path + query template with placeholders |
| `query_keys` | Query parameter names (no values) |
| `body_keys` | Form/JSON/multipart field names (no values) |
| `module` | Planner, Berichten, Vakken, … |
| `observed_on` | `school-a.smartschool.be parent/co-account` or `school-b.smartschool.be parent/co-account` when seen in Chrome; `library` when only known from `smartschool` |
| `library` | Class in `svaningelgem/smartschool`, or `unmapped` |
| `mcp_tool` | Existing MCP tool name, or `none` |
| `notes` | Optional. Hardcoded query, spelling mismatch, deprecation, … |

Optional extra: `xml_action` when several XML commands share one dispatcher path.

Library pin used for this seed was `svaningelgem/smartschool` git commit `5bb98a86`; the `library` column was checked against PyPI `0.13.0` (`bd98fd07`).

## Module status

| Module | Status | Source |
|--------|--------|--------|
| Planner (calendar + sidebar + pinned + settings + assignment detail) | catalogued | Live HAR, school A + school B parent/co-account |
| Planner calendars | catalogued | Live HAR |
| Planner ICS profiles | catalogued | Live HAR, school B |
| Labels | catalogued | Live HAR |
| Group filters | catalogued | Live HAR |
| Lesson-content assignment types | catalogued | Live HAR + library (path spelling differs) |
| Course list | catalogued | Live HAR + library `CourseList` |
| Topnav `getToken` | catalogued | Live HAR (`userID` body key) |
| Locales (i18n JSON) | catalogued | Live HAR (rev-hash pattern + plain `.json`) |
| Publishers / isbaoplatform | catalogued | Live HAR |
| Results / periods / reports / result-courses | catalogued | Live HAR (filters beyond the library query) |
| Topnav courses | catalogued | Live HAR, school B (`TopNavCourses`) |
| Messages (XML dispatcher + compose + archive) | catalogued | Live HAR for list/detail/postbox helpers; compose/archive still library-only |
| Legacy Agenda XML + future tasks | catalogued | Library only; **not** in this HAR |
| My Documents | catalogued | Library only; **not** opened in either HAR |
| Intradesk | catalogued | Live HAR, school B (root + folders + recent + favourite) |
| Course documents (HTML) | catalogued | Live HAR, school B (`FolderItem` path + `parentID` subfolder) |
| Student support | catalogued | Live HAR |
| Homepage HTML (`GET /`) + homepage dispatcher | catalogued | Live HAR |
| Auth / 2FA | catalogued | Library + live `GET /account-verification` on school B |
| Start | catalogued | Live HAR |
| Mijn kinderen / Studentcard | catalogued | Live HAR; no MCP tool |
| Vakken UI (live) | catalogued | Course list + `getCourseConfig` + course-folder HTML (Documents, Uploadzone, Weblinks, Classmates, Lpaths, Tasks, video-call, Exercises, Cooperate, Forum, course news) |
| Berichten UI (live) | catalogued | Live HAR |
| Resultaten UI (live) | catalogued | Live HAR |
| Timetable SPA | catalogued | Live HAR (`GET /timetable/api/v1/`) |
| Contact moments | catalogued | Live HAR, school A |
| Forms | catalogued | Live HAR, school A |
| Photos | catalogued | Live HAR, school A |
| LVS | catalogued | Live HAR, school A |
| News | catalogued | Live HAR, school B (school news + course `coursenews`) |
| Wiki | catalogued | Live HAR, school B (dispatcher + course `/?module=Wiki&file=index`) |
| Links | catalogued | Live HAR, school B |
| Profiel | catalogued | Live HAR, school B (`GET /?module=Profile`, `getProfiles`, `go-menu`) |
| Documenten UI (live) | catalogued | Intradesk + course Documents on school B; **mydocs** still not opened |
| Zoeken | catalogued | Live HAR, school B (`POST /search/api/v1/config` + `query`) |
| Meldingen | catalogued | Live HAR, school B (`GET /Profile/Notify` + `POST /Profile/Notify/*` settings) |

## Still open

Every visible parent/co-account nav module from the playbook is now `catalogued`. Optional leftover:

1. Open **Mijn documenten** (`/mydoc/api/v1/…`) if that tab exists beside Intradesk (still library-only).
2. Export HAR into `docs/portal-api/raw/` and merge sanitized rows if mydocs differs from the library paths.

Do **not** commit the HAR: `docs/portal-api/raw/` is in `.gitignore`, and an export holds cookies, names and grades. Do **not** crawl the portal from scripts; capture from the browser.
