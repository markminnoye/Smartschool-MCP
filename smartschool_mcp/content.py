"""
Parsers for Smartschool course content: planner element details and leerpaden.

Pure functions over the JSON or HTML Smartschool returns, so they can be tested
on recorded responses without a session.
"""

from __future__ import annotations

import html as html_lib
import re
from typing import Any

from bs4 import BeautifulSoup

# Readable names for the planner's element types.
PLANNER_TYPE_LABELS = {
    "planned-assignments": "Assignment",
    "planned-to-dos": "To-do",
    "planned-lessons": "Lesson",
    "planned-placeholders": "Timetable slot",
    "planned-lesson-cluster-moments": "Lesson cluster moment",
    "planned-lesson-cluster-assignments": "Lesson cluster assignment",
    "planned-lesson-clusters": "Lesson cluster",
    "planned-lesson-free-days": "Day off",
    "planned-meetings": "Event",
    "planned-school-activities": "School activity",
    "planned-activities": "Activity",
    "planned-generics": "Other",
    "planned-routines": "Routine",
    "planned-partner-elements": "Partner element",
    "private-planned-elements": "Private",
}

# Timetable slots without content: left out unless asked for by type.
EMPTY_SLOT_TYPES = frozenset({"planned-placeholders", "planned-lesson-cluster-moments"})

_EXAM_RE = re.compile(
    r"\b(examens?|exams?|toets(en)?|tests?|proefwerk(en)?|herexamen)\b", re.IGNORECASE
)


def type_label(element_type: str) -> str:
    return PLANNER_TYPE_LABELS.get(element_type, element_type)


def is_exam(name: str | None, assignment_type: str | None = None) -> bool:
    """Whether a planner element looks like an exam or a test, by name or type."""
    return any(_EXAM_RE.search(text) for text in (name, assignment_type) if text)


# "- " followed by a hyphen, an en dash or a bullet.
_OWN_DASH_RE = re.compile("^- [" + re.escape("-" + chr(0x2013) + chr(0x2022)) + r"]\s*")


def html_to_text(raw: str | None) -> str:
    """Readable text from a planner description: one line per paragraph."""
    if not raw:
        return ""
    soup = BeautifulSoup(raw, "html.parser")
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for li in soup.find_all("li"):
        li.insert_before("\n- ")
    for block in soup.find_all(["p", "div", "ul", "ol", "h1", "h2", "h3", "h4"]):
        block.insert_after("\n")
    # A list item whose text starts with its own dash keeps a single one.
    lines = (
        _OWN_DASH_RE.sub("- ", line.strip()) for line in soup.get_text().splitlines()
    )
    return "\n".join(line for line in lines if line)


def _link(label: Any, url: Any, kind: str) -> dict[str, Any]:
    return {"label": (label or "").strip() or None, "url": url or None, "kind": kind}


def planner_detail(detail: dict[str, Any], base_url: str) -> dict[str, Any]:
    """
    Summarise the JSON of ``GET /planner/api/v1/{type}/{platformId}/{id}``.

    Keeps what a parent needs: the description as text, the attachments to
    download and every link, whatever part of the element it comes from.
    Links in the description that Smartschool sent without an address are
    listed by title in ``links_without_url`` (typically Bookwidgets, to be
    looked up in the course's leerpaden).
    """
    period = detail.get("period") or {}
    assignment_type = (detail.get("assignmentType") or {}).get("name")
    links: list[dict[str, Any]] = []
    unlinked: list[str] = []

    description = detail.get("publicInfo") or ""
    for a in BeautifulSoup(description, "html.parser").find_all("a"):
        title = a.get_text(" ", strip=True)
        href = a.get("href")
        if href:
            links.append(_link(title or href, href, "description"))
        elif title and title not in unlinked:
            unlinked.append(title)

    for item in detail.get("weblinks") or []:
        links.append(
            _link(
                item.get("name") or item.get("title") or item.get("description"),
                item.get("url") or item.get("link"),
                "weblink",
            )
        )
    for item in detail.get("partnerWeblinks") or []:
        links.append(
            _link(
                item.get("name") or item.get("title"),
                item.get("url") or item.get("link"),
                "partner",
            )
        )
    for item in detail.get("courseLinks") or []:
        links.append(
            _link(
                item.get("name") or item.get("title"),
                item.get("url") or item.get("link"),
                "course_link",
            )
        )
    for item in detail.get("deeplinks") or []:
        meta = item.get("metadata") or {}
        link = _link(
            item.get("name") or meta.get("filename"), item.get("url"), "document"
        )
        if meta.get("type") in ("file", "folder") and meta.get("courseId"):
            # Lets download_course_document / get_course_documents fetch it.
            link["document"] = {
                "course_id": meta.get("courseId"),
                "doc_id": meta.get("docId"),
                "is_folder": meta.get("type") == "folder",
            }
        links.append(link)

    for link in links:
        if link["url"] and link["url"].startswith("/"):
            link["url"] = base_url.rstrip("/") + link["url"]

    return {
        "id": detail.get("id"),
        "platform_id": detail.get("platformId"),
        "type": detail.get("plannedElementType"),
        "type_label": type_label(detail.get("plannedElementType") or ""),
        "name": detail.get("name"),
        "is_exam": is_exam(detail.get("name"), assignment_type),
        "from": period.get("dateTimeFrom"),
        "to": period.get("dateTimeTo"),
        "courses": [c.get("name") for c in detail.get("courses") or []],
        "assignment_type": assignment_type,
        "description": html_to_text(description),
        "attachments": [
            {
                "id": a.get("id"),
                "name": a.get("fileName"),
                "size": a.get("fileSize"),
                "mime_type": a.get("mimeType"),
            }
            for a in detail.get("attachments") or []
        ],
        "links": links,
        "links_without_url": unlinked,
        "to_dos": [t.get("name") for t in detail.get("plannedToDos") or []],
    }


def _normalised(text: str | None) -> str:
    return " ".join((text or "").split()).casefold()


def content_flags(detail: dict[str, Any]) -> dict[str, bool]:
    """
    Whether a summarised planner element (``planner_detail``) has more to show.

    ``has_links`` counts the links without an address too (Bookwidgets);
    ``has_description`` is false when the description only repeats the name.
    """
    description = _normalised(detail["description"])
    return {
        "has_attachments": bool(detail["attachments"]),
        "has_links": bool(detail["links"] or detail["links_without_url"]),
        "has_description": bool(description)
        and description != _normalised(detail["name"]),
    }


# ── Leerpaden (server-rendered HTML) ──────────────────────────────────────────

_LPATH_FOLDER_RE = re.compile(r"/Lpaths/Index/Index/courseID/\d+/parentID/(\d+)")
_LPATH_VIEW_RE = re.compile(
    r"/Lpaths/Viewlpath/Index/courseID/\d+/lpathID/(\d+)(?:/parentID/(\d+))?"
)
_LPATH_STEP_RE = re.compile(
    r"/Lpaths/Viewlpath/Index/courseID/(\d+)/id/(\d+)/modules/(\w+)/lpathID/\d+"
)


def _content_rows(soup: BeautifulSoup) -> list[Any]:
    body = soup.select_one("div.smsc_cm_body")
    return body.select("div.smsc_cm_body_row") if body else []


def _shorten(text: str, limit: int = 160) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def parse_lpath_folder(page: str) -> list[dict[str, Any]]:
    """The folders and leerpaden listed on a ``/Lpaths/Index/Index/...`` page."""
    soup = BeautifulSoup(page, "html.parser")
    entries = []
    for row in _content_rows(soup):
        a = row.select_one("a[href]")
        if a is None:
            continue
        href = html_lib.unescape(a["href"])
        desc = row.select_one(".smsc_cm_body_row_block_desc")
        description = desc.get_text(" ", strip=True) if desc else ""
        name = a.get_text(" ", strip=True)
        if match := _LPATH_VIEW_RE.search(href):
            entries.append(
                {
                    "kind": "learning_path",
                    "id": int(match.group(1)),
                    "folder_id": int(match.group(2) or 0),
                    "name": name,
                    **({"description": _shorten(description)} if description else {}),
                }
            )
        elif match := _LPATH_FOLDER_RE.search(href):
            entries.append({"kind": "folder", "id": int(match.group(1)), "name": name})
    return entries


def parse_lpath_view(page: str) -> dict[str, Any]:
    """
    The sections and steps of a leerpad (``/Lpaths/Viewlpath/...`` page).

    Each step has its module (documents, weblinks, exercises, ...), the id of
    the item in that module, and ``done``: whether the pupil finished it.
    ``progress_percent`` is the share of the leerpad the pupil completed.
    """
    soup = BeautifulSoup(page, "html.parser")
    title = soup.select_one(".view_lpath_title")
    descriptions = soup.select_one(".view_lpath_descriptions")
    sections: list[dict[str, Any]] = []
    parts = soup.select_one(".view_lpath_left_parts")
    for node in parts.select(".view_lpath_body_title") if parts else []:
        link = node.select_one("a.view_lpath_body_title_link")
        if link is None:
            sections.append({"title": node.get_text(" ", strip=True), "steps": []})
            continue
        match = _LPATH_STEP_RE.search(html_lib.unescape(str(link.get("href") or "")))
        name = link.select_one(".view_lpath_body_name")
        if not sections:
            sections.append({"title": None, "steps": []})
        sections[-1]["steps"].append(
            {
                "module": match.group(3) if match else None,
                "item_id": int(match.group(2)) if match else None,
                "name": (name or link).get_text(" ", strip=True),
                "done": link.select_one(".view_lpath_body_checked .success")
                is not None,
            }
        )
    progress = soup.select_one(".view_lpath_progress_percentage")
    percent = re.search(r"(\d+)\s*%", progress.get_text()) if progress else None
    return {
        "name": title.get_text(" ", strip=True) if title else None,
        "description": descriptions.get_text("\n", strip=True) if descriptions else "",
        "progress_percent": int(percent.group(1)) if percent else None,
        "sections": sections,
    }


_EXERCISE_FOLDER_RE = re.compile(
    r"module=Exercises&file=index&courseID=\d+&parentID=(\d+)"
)
_EXTERNAL_EXERCISE_RE = re.compile(
    r"function=openExternalExercise&exerciseID=(\d+)[^\"']*?&type=(\w+)"
)


def parse_exercise_folder(page: str) -> tuple[list[int], dict[int, dict[str, Any]]]:
    """
    Sub-folder ids and external exercises (Bookwidgets, ...) of an exercises page.

    Returns the folder ids to visit and, per exercise id, its provider and the
    Smartschool address that opens it for the pupil.
    """
    soup = BeautifulSoup(page, "html.parser")
    folders: list[int] = []
    exercises: dict[int, dict[str, Any]] = {}
    for row in _content_rows(soup):
        for a in row.select("a[href]"):
            href = html_lib.unescape(a["href"])
            if match := _EXTERNAL_EXERCISE_RE.search(href):
                exercises[int(match.group(1))] = {
                    "provider": match.group(2),
                    "name": a.get_text(" ", strip=True),
                    "url": href,
                }
            elif (match := _EXERCISE_FOLDER_RE.search(href)) and match.group(1) != "0":
                folders.append(int(match.group(1)))
    return folders, exercises
