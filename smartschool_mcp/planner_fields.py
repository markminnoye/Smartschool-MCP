"""Planner id, description, and upload-folder parsing.

``PlannedElement`` at ``markminnoye/smartschool@517de70`` keeps ``id`` and
drops every other unknown key. Captured calendar fixtures have no
``description`` and no ``uploadFolders``. The website sends
``includes=icon,courses,locations,upload-folders,labels``; the JSON names
below are inferred from that query and from the to-do body key ``publicInfo``.
They are not verified against a live login.

A matching library change exists as
``docs/upstream/planner-item-fields-sr-95.patch`` (local commit
``3e8fe3b`` on ``planner-item-fields-sr-95``, which could not be pushed).
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from smartschool import convert_to_datetime

PLANNER_ATTACHMENT_INCLUDES = "icon,courses,locations,upload-folders,labels"

# Catalogued assignment-detail GET. Response body is not in the fixtures.
ASSIGNMENT_DETAIL_TYPES = frozenset(
    {
        "planned-assignments",
        "planned-lesson-cluster-assignments",
    }
)

_TAG_RE = re.compile(r"<[^>]+>")
_ELEMENT_ID_RE = re.compile(
    r"(?i)^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


def element_id_or_none(value: str) -> str | None:
    text = value.strip()
    if _ELEMENT_ID_RE.fullmatch(text):
        return text
    return None


def planner_query(
    start: date,
    end: date,
    types: str | None,
    includes: str | None,
) -> dict[str, str]:
    """Same ``from``/``to``/optional ``types``/``includes`` query as PlannedElements."""
    data = {"from": start.isoformat(), "to": end.isoformat()}
    if types:
        data["types"] = types
    if includes:
        data["includes"] = includes
    return data


def _text(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    return ""


def _as_id(value: object) -> str:
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, (str, int)):
        return str(value).strip()
    return ""


def _as_size(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _first_text(data: dict[str, Any], *keys: str) -> str:
    for key in keys:
        text = _text(data.get(key))
        if text:
            return text
    return ""


def _first_list(data: dict[str, Any], *keys: str) -> list[Any] | None:
    for key in keys:
        value = data.get(key)
        if isinstance(value, list):
            return value
    return None


def _plain_text(value: str) -> str:
    return " ".join(_TAG_RE.sub(" ", value).split())


def _normalize_file(raw: dict[str, Any]) -> dict[str, Any]:
    revision = raw.get("currentRevision")
    revision = revision if isinstance(revision, dict) else {}
    links = raw.get("links")
    links = links if isinstance(links, dict) else {}
    download = _first_text(
        raw, "downloadUrl", "downloadLink", "download_url", "href", "url"
    )
    if not download:
        download = _first_text(links, "download", "downloadUrl", "href")
    mime = _first_text(raw, "mimeType", "mime", "contentType") or _first_text(
        revision, "mimeType", "mime"
    )
    size = raw.get("size", raw.get("fileSize", revision.get("fileSize")))
    revision_id = (
        raw.get("currentRevisionId") or revision.get("id") or raw.get("revisionId")
    )
    return {
        "id": _as_id(raw.get("id", raw.get("fileId"))),
        "name": _first_text(raw, "name", "filename", "fileName", "title"),
        "mime_type": mime,
        "size": _as_size(size),
        "download_url": download,
        "revision_id": _as_id(revision_id),
    }


def _looks_like_file(raw: dict[str, Any]) -> bool:
    if _first_list(raw, "files", "uploads", "items", "attachments") is not None:
        return False
    if _first_text(
        raw,
        "filename",
        "fileName",
        "downloadUrl",
        "downloadLink",
        "download_url",
        "href",
        "url",
        "mimeType",
        "mime",
        "contentType",
    ):
        return True
    return bool(
        raw.get("fileId") or raw.get("currentRevision") or raw.get("currentRevisionId")
    )


def _coerce_folder_list(value: object) -> list[Any]:
    if isinstance(value, list):
        return value
    if not isinstance(value, dict):
        return []
    nested = _first_list(
        value, "folders", "uploadFolders", "items", "data", "files", "uploads"
    )
    if nested is not None:
        return nested
    return [value]


def normalize_upload_folders(value: object) -> list[dict[str, Any]]:
    """Map an unverified upload-folder payload onto a stable folder list."""
    folders: list[dict[str, Any]] = []
    loose_files: list[dict[str, Any]] = []
    for raw in _coerce_folder_list(value):
        if not isinstance(raw, dict):
            continue
        file_list = _first_list(raw, "files", "uploads", "items", "attachments")
        if file_list is None:
            if _looks_like_file(raw):
                loose_files.append(_normalize_file(raw))
            elif _as_id(raw.get("id")):
                folders.append(
                    {
                        "id": _as_id(raw.get("id")),
                        "name": _first_text(raw, "name", "title"),
                        "files": [],
                    }
                )
            continue
        folders.append(
            {
                "id": _as_id(raw.get("id")),
                "name": _first_text(raw, "name", "title"),
                "files": [
                    _normalize_file(item)
                    for item in file_list
                    if isinstance(item, dict)
                ],
            }
        )
    if loose_files:
        folders.append({"id": "", "name": "", "files": loose_files})
    return folders


def prepare_planned_element(payload: dict[str, Any]) -> dict[str, Any]:
    """Keep description and upload folders that PlannedElement would drop."""
    data = dict(payload)
    public_info = data.get("publicInfo")
    if not isinstance(public_info, str) or not public_info.strip():
        public_info = ""

    description = data.get("description")
    if not isinstance(description, str) or not description.strip():
        description = public_info
        if not description:
            for key in ("descr", "content", "info", "remarks"):
                candidate = data.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    description = candidate.strip()
                    break
    else:
        description = description.strip()

    raw_folders = None
    for key in ("uploadFolders", "upload_folders", "attachments"):
        if key in data:
            raw_folders = data.get(key)
            break
    if raw_folders is not None:
        data["uploadFolders"] = normalize_upload_folders(raw_folders)
    else:
        data["uploadFolders"] = []
    data["description"] = description
    data["publicInfo"] = public_info
    return data


def _fmt_when(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = convert_to_datetime(value)
    except Exception:
        return value.strip()
    if isinstance(parsed, datetime):
        return parsed.strftime("%Y-%m-%d %H:%M")
    return value.strip()


def _names_from_courses(raw: dict[str, Any]) -> list[str]:
    courses = raw.get("courses")
    if not isinstance(courses, list):
        return []
    names: list[str] = []
    for course in courses:
        if isinstance(course, dict):
            name = _text(course.get("name"))
            if name:
                names.append(name)
        elif isinstance(course, str) and course.strip():
            names.append(course.strip())
    return names


def _location_titles(raw: dict[str, Any]) -> list[str]:
    locations = raw.get("locations")
    if not isinstance(locations, list):
        return []
    titles: list[str] = []
    for location in locations:
        if isinstance(location, dict):
            title = _text(location.get("title")) or _text(location.get("name"))
            if title:
                titles.append(title)
        elif isinstance(location, str) and location.strip():
            titles.append(location.strip())
    return titles


def _organiser_names(raw: dict[str, Any]) -> list[str]:
    organisers = raw.get("organisers")
    users: list[Any] = []
    if isinstance(organisers, dict) and isinstance(organisers.get("users"), list):
        users = organisers["users"]
    names: list[str] = []
    for user in users:
        if not isinstance(user, dict):
            continue
        name = user.get("name")
        if isinstance(name, dict):
            label = _first_text(
                name, "startingWithFirstName", "starting_with_first_name"
            )
            if label:
                names.append(label)
        elif isinstance(name, str) and name.strip():
            names.append(name.strip())
    return names


def _public_folders(folders: list[dict[str, Any]]) -> list[dict[str, Any]]:
    public: list[dict[str, Any]] = []
    for folder in folders:
        files = []
        for item in folder.get("files") or []:
            if not isinstance(item, dict):
                continue
            files.append(
                {
                    "id": item.get("id") or "",
                    "name": item.get("name") or "",
                    "mime_type": item.get("mime_type") or "",
                    "size": item.get("size"),
                    "has_download_url": bool(item.get("download_url")),
                }
            )
        public.append(
            {
                "id": folder.get("id") or "",
                "name": folder.get("name") or "",
                "files": files,
            }
        )
    return public


def calendar_element_dict(raw: dict[str, Any]) -> dict[str, Any]:
    """One planner row for get_schedule / get_planned_elements."""
    prepared = prepare_planned_element(raw)
    raw_period = raw.get("period")
    period: dict[str, Any] = raw_period if isinstance(raw_period, dict) else {}
    assignment = raw.get("assignmentType")
    assignment_name = None
    if isinstance(assignment, dict):
        assignment_name = _text(assignment.get("name")) or None
    platform_id = raw.get("platformId")
    if isinstance(platform_id, bool) or not isinstance(platform_id, int):
        platform_id = None
    element_id = _as_id(raw.get("id")) or None
    return {
        "id": element_id,
        "platform_id": platform_id,
        "name": _text(raw.get("name")),
        "description": _plain_text(_text(prepared.get("description"))),
        "type": _text(raw.get("plannedElementType")) or None,
        "from": _fmt_when(period.get("dateTimeFrom")),
        "to": _fmt_when(period.get("dateTimeTo")),
        "whole_day": period.get("wholeDay"),
        "color": _text(raw.get("color")) or None,
        "courses": _names_from_courses(raw),
        "locations": _location_titles(raw),
        "organisers": _organiser_names(raw),
        "unconfirmed": raw.get("unconfirmed")
        if isinstance(raw.get("unconfirmed"), bool)
        else None,
        "pinned": raw.get("pinned") if isinstance(raw.get("pinned"), bool) else None,
        "assignment_type": assignment_name,
        "upload_folders": _public_folders(prepared.get("uploadFolders") or []),
    }


def _user_id(session: Any) -> str:
    user = getattr(session, "authenticated_user", None)
    if isinstance(user, dict) and user.get("id") not in (None, ""):
        return str(user["id"])
    raise ValueError("Planner user id is missing")


def fetch_calendar_raw(
    session: Any,
    start: date,
    end: date,
    types: str | None,
    includes: str | None,
) -> list[dict[str, Any]]:
    """GET the website calendar. One call; does not use PlannedElements."""
    payload = session.json(
        f"/planner/api/v1/planned-elements/user/{_user_id(session)}",
        data=planner_query(start, end, types, includes),
    )
    if not isinstance(payload, list):
        raise ValueError("Planner calendar response was not a list")
    rows: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("Planner calendar item was not an object")
        rows.append(item)
    return rows


def fetch_calendar(
    session: Any,
    start: date,
    end: date,
    types: str | None,
    includes: str | None,
) -> list[dict[str, Any]]:
    return [
        calendar_element_dict(item)
        for item in fetch_calendar_raw(session, start, end, types, includes)
    ]


def fetch_assignment_detail(
    session: Any, platform_id: int, element_id: str
) -> dict[str, Any] | None:
    """Assignment detail. Path is catalogued; body is unverified."""
    if isinstance(platform_id, bool) or not isinstance(platform_id, int):
        return None
    path = f"/planner/api/v1/planned-assignments/{platform_id}/{element_id}"
    try:
        payload = session.json(path)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    return calendar_element_dict(payload)


def file_download(
    raw_rows: list[dict[str, Any]], element_id: str, file_id: str
) -> dict[str, Any] | None:
    """Find one upload file, including its download URL, inside raw elements."""
    for raw in raw_rows:
        if _as_id(raw.get("id")).lower() != element_id.lower():
            continue
        prepared = prepare_planned_element(raw)
        source = _source_file(raw, file_id)
        for folder in prepared.get("uploadFolders") or []:
            for item in folder.get("files") or []:
                if _as_id(item.get("id")).lower() == file_id.lower():
                    found = dict(item)
                    found["folder_id"] = folder.get("id") or ""
                    found["folder_name"] = folder.get("name") or ""
                    if source is not None:
                        found["_source"] = source
                    return found
    return None


def _source_file(raw: dict[str, Any], file_id: str) -> dict[str, Any] | None:
    """Original file object, so unknown URL fields stay available."""
    folders = None
    for key in ("uploadFolders", "upload_folders", "attachments"):
        if key in raw:
            folders = raw.get(key)
            break
    for folder in _coerce_folder_list(folders):
        if not isinstance(folder, dict):
            continue
        file_list = _first_list(folder, "files", "uploads", "items", "attachments")
        candidates = file_list if file_list is not None else [folder]
        for item in candidates:
            if not isinstance(item, dict):
                continue
            item_id = _as_id(item.get("id", item.get("fileId"))).lower()
            if item_id == file_id.lower():
                return item
    return None


def portal_download_target(session: Any, url: str) -> str | None:
    """Accept a same-host path from the planner payload. Reject anything else."""
    if not isinstance(url, str):
        return None
    target = url.strip()
    if not target or ".." in target or target.startswith("//"):
        return None
    if target.startswith("/"):
        return target
    base = session.create_url("/")
    if isinstance(base, str) and target.startswith(base):
        return target
    return None


_PATH_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_SECRET_KEY_RE = re.compile(
    r"(?i)cookie|token|password|secret|authorization|csrf|session"
)
_DIAGNOSTIC_KEY_RE = re.compile(
    r"(?i)upload|attach|folder|file|document|link|url|href|download"
)


def _path_id(value: object) -> str | None:
    text = _as_id(value)
    if _PATH_ID_RE.fullmatch(text):
        return text
    return None


def _public_download_path(target: str) -> str:
    """Path without query or fragment, so a signed URL is not echoed."""
    return target.split("?", 1)[0].split("#", 1)[0]


def planner_download_candidates(
    session: Any,
    *,
    element_id: str,
    file_info: dict[str, Any],
    platform_id: int | None,
) -> list[str]:
    """Same-host GETs to try for one planner file.

    Nothing here is live-verified. A URL already on the file is tried first.
    The rest are planner-shaped paths built from ids in that same payload.
    My Documents and Intradesk are different modules and are not guessed:
    an id collision there could save the wrong file.
    """
    paths: list[str] = []

    def add(url: object) -> None:
        if not isinstance(url, str):
            return
        target = portal_download_target(session, url)
        if target and target not in paths:
            paths.append(target)

    add(file_info.get("download_url"))
    source = file_info.get("_source")
    if isinstance(source, dict):
        for url in _url_strings(source):
            add(url)

    file_id = _path_id(file_info.get("id"))
    revision_id = _path_id(file_info.get("revision_id"))
    folder_id = _path_id(file_info.get("folder_id"))
    element = _path_id(element_id)
    platform = None
    if (
        isinstance(platform_id, int)
        and not isinstance(platform_id, bool)
        and platform_id > 0
    ):
        platform = str(platform_id)
    if file_id:
        add(f"/planner/api/v1/files/{file_id}/download")
        if revision_id:
            add(f"/planner/api/v1/files/{file_id}/revisions/{revision_id}/download")
        if folder_id:
            add(f"/planner/api/v1/upload-folders/{folder_id}/files/{file_id}/download")
        if element:
            add(f"/planner/api/v1/planned-elements/{element}/files/{file_id}/download")
        if platform and element:
            add(
                "/planner/api/v1/planned-assignments/"
                f"{platform}/{element}/files/{file_id}/download"
            )
    return paths


def _url_strings(node: object, *, depth: int = 0) -> list[str]:
    found: list[str] = []
    if depth > 5:
        return found
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(key, str) and _SECRET_KEY_RE.search(key):
                continue
            if isinstance(value, str):
                text = value.strip()
                if (
                    text.startswith("/")
                    or text.startswith("http://")
                    or text.startswith("https://")
                ):
                    found.append(text)
            else:
                found.extend(_url_strings(value, depth=depth + 1))
    elif isinstance(node, list):
        for item in node[:20]:
            found.extend(_url_strings(item, depth=depth + 1))
    return found


def json_diagnostic(value: object, *, max_keys: int = 80) -> dict[str, Any]:
    """Key paths and URL-like values. Query strings and secret keys are dropped."""
    keys: list[str] = []
    urls: list[dict[str, str]] = []

    def walk(node: object, path: str, depth: int) -> None:
        if len(keys) >= max_keys or depth > 6:
            return
        if isinstance(node, dict):
            for key, child in node.items():
                if not isinstance(key, str):
                    continue
                child_path = f"{path}.{key}" if path else key
                keys.append(child_path)
                if _SECRET_KEY_RE.search(key):
                    continue
                if isinstance(child, str):
                    safe = _public_url(child)
                    if safe:
                        urls.append({"key": child_path, "value": safe})
                else:
                    walk(child, child_path, depth + 1)
        elif isinstance(node, list) and node:
            walk(node[0], f"{path}[]", depth + 1)

    walk(value, "", 0)
    return {"keys": keys, "url_fields": urls}


def _public_url(value: str) -> str | None:
    text = value.strip()
    if not text or text.startswith("//") or ".." in text:
        return None
    if not (
        text.startswith("/")
        or text.startswith("http://")
        or text.startswith("https://")
    ):
        return None
    cut = _public_download_path(text)
    if len(cut) > 300:
        cut = cut[:300]
    return cut


def attachment_diagnostic(raw: dict[str, Any]) -> dict[str, Any]:
    """Top-level keys, plus key/URL detail for attachment-shaped fields."""
    element_keys = [key for key in raw if isinstance(key, str)]
    fields: dict[str, Any] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or _DIAGNOSTIC_KEY_RE.search(key) is None:
            continue
        if _SECRET_KEY_RE.search(key):
            continue
        fields[key] = json_diagnostic(value)
    return {"element_keys": element_keys, "fields": fields}
