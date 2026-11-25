# Gap: website vs `smartschool` vs MCP

Observation only. The list at the bottom is a **draft priority** for the `smartschool` library, not planned MCP work.

Sources: HAR `docs/portal-api/raw/school-b.smartschool.be.har` (gitignored), overwritten 2026-09-17. First capture: `school-a.smartschool.be` + `school-b.smartschool.be`. Second capture: school B–only Intradesk / Zoeken / Vakken course folders. Third capture: school B Meldingen (`/Profile/Notify`). Library: `svaningelgem/smartschool` 0.13.0 (`bd98fd07`).

## Same path, different query

| Path | Website | Library | MCP |
|------|---------|---------|-----|
| `GET /planner/api/v1/planned-elements/user/{userId}` | Calendar: `from`, `to` only. Other live variants: `from`+`to`+`types`; `from`+`to`+`includes`; sidebar `from`+`to`+`includes`+`types`. Includes seen: `icon,courses,locations,upload-folders,labels`. Filtered types include `planned-to-dos`, `planned-lesson-cluster-assignments`, `planned-assignments`, `planned-generics`, `planned-lesson-free-days`, `planned-meetings`, `planned-school-activities`. | Default: `from`, `to`, `types=planned-assignments,planned-to-dos`. `element_types=None` omits `types`. No `includes` (issue #237). | `get_schedule` (one day) and `get_planned_elements` (range; optional types/includes) via the raw GET |
| `GET /results/api/v1/evaluations/` | `pageNumber`, `itemsOnPage`, plus UI filters `componentIds`, `courseIds`, `periodIds` | `pageNumber`, `itemsOnPage` only | `get_results` uses the library as-is |
| `POST /Topnav/Node/getToken` | JSON/form body key `userID` | Not wrapped | none |

This timetable gap is closed in the MCP via the raw calendar GET. Library 0.13.0 still defaults to assignments and to-dos; pass `element_types=None` for the roster.

## Same family, different path

| Website | Library |
|---------|---------|
| `/lesson-content/api/v1/assignments/applicable-assignment-types` | `/lesson-content/api/v1/assignments/applicable-assigment-types` (typo; 0.13.0 still uses this) |
| `/results/api/v1/reports/` (trailing slash) | `/results/api/v1/reports` |
| `/intradesk/api/v1/{platformId}/directory-listing/forTreeOnlyFolders` (root, no id) | Library always appends `/{folderId}` (empty string for root → trailing slash) |

## Site, not in the library

Planner shell and writes (beyond the planned-elements GETs):

| Path | Notes |
|------|--------|
| `GET /planner/api/v1/calendars/accessible-platform-calendars` | Planner shell |
| `GET /planner/api/v1/calendars/readable-platform-calendars` | Planner shell |
| `GET /planner/api/v1/planned-assignments/{platformId}/{assignmentId}` | Assignment detail |
| `POST /planner/api/v1/planned-lesson-clusters/list` | Cluster list |
| `POST /planner/api/v1/planned-to-dos/` | Create/save to-do |
| `POST /planner/api/v1/user-settings/save/join-planned-elements` | UI setting |
| `POST /planner/api/v1/user-settings/save/todo` | UI setting |
| `POST /planner/api/v1/user-settings/save/view` | UI setting |
| `POST /planner/api/v1/ics/profile` | ICS profile save (school B) |
| `GET /planner/api/v1/ics/profile/own` | ICS profile (school B) |
| `GET /labels/api/v1/labels/platform` | Planner shell |
| `GET /labels/api/v1/labels/user` | Planner shell |
| `GET /labels/api/v1/groups/` | Planner shell |
| `GET /group-filters/api/v1/properties/{propertyId}` | Planner/template property ids (see catalog) |
| `GET /group-filters/api/v1/properties/{propertyId}/quick-filter` | Same ids |
| `GET /timetable/api/v1/` | Timetable SPA bootstrap; not Agenda XML |

Start / studentcard / LVS / other nav (unmapped unless noted):

| Path | Notes |
|------|--------|
| `POST /?module=Homepage&file=dispatcher` | `datepicker/get calendar items`, `news/news list` |
| `POST /Homepage/Setting/getConfig` | Start shell |
| `POST /Homepage/Pushwizard/getwizardconfig` | Push wizard |
| `POST /Homepage/Pushwizard/saveactivation` | Push wizard |
| `POST /Studentcard/Student/getStudents` | Child list on Mijn kinderen (XHR header required; the current child can have `accountID` 0, the topnav `gotourl` links carry the real ids) |
| `GET /Studentcard/Chain/gotourl/accountID/{accountId}` | Child switch HTML |
| `POST /Studentcard/*` (config, LVS, messages, presence, evaluations, reports) | Child overview widgets |
| `POST /?module=LVS&file=dispatcher` | Several `lvs` / `lvs_groups` / `lvs_pupils` actions; the library wraps `lvs_pupils/getUserData` and `getItemParams` (`StudentNotes`, `StudentAbsences`, `StudentNoteDetails`) |
| `GET`/`POST /?module=LVS&file=jqDispatcher_*` | Settings and group tree helpers |
| `POST /?module=LVS&file=treeBuilder` | Group tree |
| `POST /LVS/Config/getConfig` | LVS config |
| `POST /LVS/Pupilpresences` / `Pupilpresencesgraphs` | Presence |
| `GET /contact-moments/api/v1/contact-moments/` | List |
| `GET /contact-moments/api/v1/contact-moments/active` | Active |
| `GET /alert/api/v1/contact-moments-all/{userId}/calculate` | Alert calc |
| `GET /forms/api/v1/…` | Participant forms + rounds/entries/answers |
| `GET /photos/api/v1/albums/` / `configuration/` / `favourites/` | Photos |
| `POST /goal/selector/api/v1/leerplannen/results/{date}` | Goals from a results screen |
| `POST /?module=News&file=dispatcher` | `overview/getGroupsXML`, `overview/loadMsg` (school B) |
| `POST /?module=Wiki&file=dispatcher` | `wiki/get wikis` (school B) |
| `GET /links/api/v1/` / `configuration` | Links (school B) |
| `GET /topnav/getProfiles` / `/topnav/go-menu` | Profile shell (school B) |
| `GET /results/api/v1/components/` | Results UI |
| `GET /results/api/v1/feedback/` | Results UI |
| `POST /?module=Messages&file=dispatcher` extra actions | `postboxes/calculate_postbox_counters`, `loadtooltip`, `smartbox list`, `quickactions/reloadunreadmessages`, `maintenance/save postbox` |
| `POST /Topnav/Node/getToken` | SPA token; do not wrap as a data API |
| `GET /Publishers/Index/isbaoplatform` | Planner/shell |
| `GET /smsc/locales/{locale}/{bundle}.rev-{hash}.json` | i18n, not student data |
| `GET /smsc/locales/{locale}/{bundle}.json` | i18n without rev-hash |
| `GET /intradesk` | Intradesk HTML shell (school B) |
| `GET /intradesk/api/v1/recent` | Recent files |
| `GET /intradesk/api/v1/{platformId}/directory-listing/forTreeOnlyFolders` | Root listing without folderId |
| `POST /intradesk/api/v1/{platformId}/files/{fileId}/mark-as-favourite` | Empty JSON body |
| `POST /intradesk/api/v1/{platformId}/files/{fileId}/discard-as-favourite` | Empty JSON body; library MyDocs uses `unmark-as-favourite` on `/mydoc` |
| `POST /search/api/v1/config` | Form keys `pathname`, `query` |
| `POST /search/api/v1/query` | JSON keys `allCommunityPlatforms`, `module`, `queryString`, `since` |
| `GET /?module=Uploadzone&file=index` / `POST …file=dispatcher` / `POST …file=tree` | Course uploadzone HTML + XML (`page/start`, `page/uploadmapdetail`) |
| `GET /Weblinks/Index/Index/…` `GET /Classmates/Index/Index/…` `GET /Lpaths/Index/Index/…` `GET /Tasks/Index/Index/…` | Vakken course HTML shells |
| `GET /course/video-call/…` + `GET /course/api/v1/video-call/{courseId}/{platformId}` + `/info` + `GET /video-call/api/v1/services` | Video-call |
| `GET /planner/course/courseID/{courseId}/ssID/{platformId}` | Planner course HTML |
| `GET /planner/api/v1/course-management/courses/{courseId}/{platformId}/teachers` | Course teachers |
| `GET /planner/api/v1/planned-elements/search/course-links/filters` | Course-link search filters |
| `GET /?module=News&file=coursenews` | Course news HTML |
| `GET /?module=Exercises&file=index` / `Cooperate` / `Forum&file=showforum` / `Wiki&file=index` | Other Vakken course HTML |
| `GET /Profile/Notify` | Meldingen HTML shell (school B) |
| `POST /Profile/Notify/getActionSettings` | Empty body |
| `POST /Profile/Notify/getEventSettings` | Empty body |
| `POST /Profile/Notify/getMobileDevices` | Empty body |
| `POST /Profile/Notify/saveShowNotifyAlerts` | Form key `value` |
| `POST /Profile/Notify/saveSetting` | Form keys `name`, `type`, `value`, optional `extra[startTime]` / `extra[endTime]` |
| `POST /Profile/Notify/saveAllowPushNotifications` | Form keys `allowPushNotifications`, `applicationID` |
| `GET /?module=Profile` | Profile HTML shell (school B page) |
| `GET /?module=Messages` (`msgID`) | HTML message view from the Notify click-through |

This HAR’s **filtered** calls also requested `planned-generics`, `planned-lesson-free-days`, `planned-meetings`. Library docs now list those types; the model is still a generic `PlannedElement`.

## Library, not (anymore) on the site in this HAR

These are still called by the library. They did **not** appear in this live HAR. That does not prove they are dead everywhere — only that **this** parent/co-account session did not use them.

| Path | Library | Deprecation candidate? |
|------|---------|------------------------|
| `POST /?module=Agenda&file=dispatcher` (`get lessons` / `get hours` / `get moment info`) | `SmartschoolLessons`, `SmartschoolHours`, `SmartschoolMomentInfos` | **Yes** for timetable. Schoolagenda was read-only from 2022 and removed 31 Aug 2025. On school A the dispatcher returns no lessons while Planner is full. Still absent from this broader HAR. |
| `POST /Agenda/Futuretasks/getFuturetasks` | `FutureTasks` | Likely. Sidebar data is Planner `types=…`. |
| `GET /mydoc/api/v1/…` and upload helpers | `MyDocsFolder` / `MyDocsFile` | Unknown — personal mydocs tab still not opened (Intradesk and Meldingen were) |
| `GET /intradesk/api/v1/{platformId}/files/{fileId}/download` | `IntradeskFile` | Unknown — listing/favourite were opened; download was not |
| `GET /Documents/Index/Index/courseID/{courseId}/ssID/{platformId}` | `FolderItem` | **Seen live** on school B (plus `parentID/{folderId}` subfolder) |
| Messages compose / searchUsers / archive / attachment download / mark unread / label / trash | `MessageComposerForm` and related | Unknown — inbox list/detail were opened; compose was not |
| `POST /login` / `POST /account-verification` / `2fa/api/v1/…` | `Smartschool` | Trusted-browser switch (2026-09-22 HAR): OTP 302 to relative `/Studentcard`, no login POST. New-device sessions may still see `GET /account-verification`. |

## MCP column (info only)

Do not invent extra MCP tools from the leftover catalog (calendars, labels, …).

| MCP tool | Library / route | Website match |
|----------|-----------------|---------------|
| `get_schedule` | Agenda lessons, then the planner calendar GET when the agenda is empty | Planner lessons as on the website calendar |
| `get_future_tasks` | `FutureTasks` (Agenda JSON), then planner `planned-assignments` / `planned-to-dos` when that list is empty | Legacy first; planner on schools that moved |
| `get_planned_elements` | Planner calendar GET with `includes` | **Yes**: same call as the calendar |
| `get_planned_element_details` / `download_planned_attachment` | `GET /planner/api/v1/{elementType}/{platformId}/{elementId}` | Element detail as on the website |
| `get_course_documents` / `download_course_document` | `TopNavCourses` + `FolderItem` | Documenten HTML listing and `/Documents/Download/Index/...` |
| `get_learning_paths` / `get_learning_path` | `/Lpaths/...` HTML | Leerpaden overview; progress is read, not changed |
| `get_courses` | `Courses` (`/results/api/v1/courses/`) | Live results course list; not the Planner `course-list` call |
| `get_results` / `get_periods` / `get_reports` | Results APIs | Live; library evaluations query is a subset of the UI filters |
| `get_student_notes` / `get_student_absences` | `StudentNotes`, `StudentNoteDetails`, `StudentAbsences` (LVS dispatcher) | Pupil file |
| `get_messages` / `get_attachments` / `download_attachment` | Messages XML | Inbox list/detail live; attachments/download not in this HAR |
| `get_student_support_links` | `StudentSupportLinks` | Live |
| `get_homepage_blocks` / `download_homepage_image` | none (HTML `GET /`) | Live Start page; homepage dispatcher XML is extra and unmapped |
| *(none)* | — | Mijn kinderen (list/switch), calendars, labels, group-filters, timetable SPA, studentcard widgets, forms, photos, contact-moments, Intradesk, Zoeken, Uploadzone, video-call, Meldingen (`/Profile/Notify`). Pinned elements have no library class in 0.13.0. |

When changing planner tools: mirror the website query (`from`, `to`, optional `types`, optional `includes`) and pass through `plannedElementType` / `period` rather than reshaping into Agenda names.

## Draft upstream list

Remaining gaps in `smartschool` 0.13.0:

1. `PlannedElements` default still filters to assignments and to-dos (MCP omits `types` itself).
2. ~~Wire docs for types seen on the wire~~ (0.13.0 docstrings cover more types).
3. Pinned endpoint `GET /planner/api/v1/planned-elements/pinned` — 0.13.0 has no `PinnedPlannedElements`.
4. Fix `ApplicableAssignmentTypes` path (`applicable-assigment-types` typo).
5. **Calendars, labels, group-filters** as thin clients of the catalogued paths (needed for a faithful Planner shell; lower than the roster GET).
6. **Results list query** — optional `componentIds`, `courseIds`, `periodIds`; reports trailing slash.
7. ~~Mark Agenda XML + `FutureTasks` as deprecated~~ (library docs in 0.13.0).
8. `includes` on the list call (upstream issue #237) and `uploadFolder` on the model.

Not in scope for upstream: partner OAuth API, SOAP `/Webservices/V3`, wrapping `getToken`, committing fixtures with student JSON.
