# Portal API inventory

This tree maps the **logged-in Smartschool website** (portal REST / XML), not the official partner API and not SOAP `/Webservices/V3`.

Agents: start at [`playbook.md`](playbook.md), then [`catalog.yml`](catalog.yml), then [`gap.md`](gap.md). Product MCP tools stay unchanged until the catalog is complete **and** upstream [`svaningelgem/smartschool`](https://github.com/svaningelgem/smartschool) covers the gaps.

## How complete is this crawl?

Three parent/co-account HAR exports from 2026-09-17 share the gitignored path `docs/portal-api/raw/dering.smartschool.be.har` (the file was overwritten each time). The first capture covered **De Pass** (`depass.smartschool.be`) and **De Ring** (`dering.smartschool.be`). The second is De Ring–only Intradesk, Zoeken, and Vakken course folders. The third is De Ring **Meldingen** (`/Profile/Notify`) plus a click-through into a message. Nav modules that appear in those HARs are `catalogued` below. Library-covered modules that were **not** opened stay catalogued from the installed `smartschool` package only.

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
| `observed_on` | `depass.smartschool.be parent/co-account` or `dering.smartschool.be parent/co-account` when seen in Chrome; `library` when only known from `smartschool` |
| `library` | Class in `svaningelgem/smartschool`, or `unmapped` |
| `mcp_tool` | Existing MCP tool name, or `none` |
| `notes` | Optional. Hardcoded query, spelling mismatch, deprecation, … |

Optional extra: `xml_action` when several XML commands share one dispatcher path.

Library pin used for this seed: `svaningelgem/smartschool` git commit `5bb98a86` (installed dist reports `0.7.0`).

## Module status

| Module | Status | Source |
|--------|--------|--------|
| Planner (calendar + sidebar + pinned + settings + assignment detail) | catalogued | Live HAR, De Pass + De Ring parent/co-account |
| Planner calendars | catalogued | Live HAR |
| Planner ICS profiles | catalogued | Live HAR, De Ring |
| Labels | catalogued | Live HAR |
| Group filters | catalogued | Live HAR |
| Lesson-content assignment types | catalogued | Live HAR + library (path spelling differs) |
| Course list | catalogued | Live HAR + library `CourseList` |
| Topnav `getToken` | catalogued | Live HAR (`userID` body key) |
| Locales (i18n JSON) | catalogued | Live HAR (rev-hash pattern + plain `.json`) |
| Publishers / isbaoplatform | catalogued | Live HAR |
| Results / periods / reports / result-courses | catalogued | Live HAR (filters beyond the library query) |
| Topnav courses | catalogued | Live HAR, De Ring (`TopNavCourses`) |
| Messages (XML dispatcher + compose + archive) | catalogued | Live HAR for list/detail/postbox helpers; compose/archive still library-only |
| Legacy Agenda XML + future tasks | catalogued | Library only; **not** in this HAR |
| My Documents | catalogued | Library only; **not** opened in either HAR |
| Intradesk | catalogued | Live HAR, De Ring (root + folders + recent + favourite) |
| Course documents (HTML) | catalogued | Live HAR, De Ring (`FolderItem` path + `parentID` subfolder) |
| Student support | catalogued | Live HAR |
| Homepage HTML (`GET /`) + homepage dispatcher | catalogued | Live HAR |
| Auth / 2FA | catalogued | Library + live `GET /account-verification` on De Ring |
| Start | catalogued | Live HAR |
| Mijn kinderen / Studentcard | catalogued | Live HAR; MCP `get_children` + `switch_child` wrap list/switch |
| Vakken UI (live) | catalogued | Course list + `getCourseConfig` + course-folder HTML (Documents, Uploadzone, Weblinks, Classmates, Lpaths, Tasks, video-call, Exercises, Cooperate, Forum, course news) |
| Berichten UI (live) | catalogued | Live HAR |
| Resultaten UI (live) | catalogued | Live HAR |
| Timetable SPA | catalogued | Live HAR (`GET /timetable/api/v1/`) |
| Contact moments | catalogued | Live HAR, De Pass |
| Forms | catalogued | Live HAR, De Pass |
| Photos | catalogued | Live HAR, De Pass |
| LVS | catalogued | Live HAR, De Pass |
| News | catalogued | Live HAR, De Ring (school news + course `coursenews`) |
| Wiki | catalogued | Live HAR, De Ring (dispatcher + course `/?module=Wiki&file=index`) |
| Links | catalogued | Live HAR, De Ring |
| Profiel | catalogued | Live HAR, De Ring (`GET /?module=Profile`, `getProfiles`, `go-menu`) |
| Documenten UI (live) | catalogued | Intradesk + course Documents on De Ring; **mydocs** still not opened |
| Zoeken | catalogued | Live HAR, De Ring (`POST /search/api/v1/config` + `query`) |
| Meldingen | catalogued | Live HAR, De Ring (`GET /Profile/Notify` + `POST /Profile/Notify/*` settings) |

## What the user must still do in the browser

Every visible parent/co-account nav module from the playbook is now `catalogued`. Optional leftover:

1. Open **Mijn documenten** (`/mydoc/api/v1/…`) if that tab exists beside Intradesk (still library-only).
2. Export HAR into `docs/portal-api/raw/` and merge sanitized rows if mydocs differs from the library paths.
3. Phase 4 (Planner 1-to-1 in the library + MCP tools) is implemented locally; mydocs can land later. The library branch still needs a GitHub push/PR to `svaningelgem/smartschool`.

Do **not** commit the HAR. Do **not** crawl the portal from ad-hoc scripts. The opt-in smoke test [`scripts/smoke_portal.py`](../../scripts/smoke_portal.py) is the exception: same login+cookies as the MCP, read-only, no bodies.

```bash
uv run python scripts/smoke_portal.py              # legacy library first, then HAR GETs
uv run python scripts/smoke_portal.py --legacy-only
PORTAL_SMOKE=1 uv run pytest -m integration
```
