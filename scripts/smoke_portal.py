#!/usr/bin/env python3
"""Live read-only smoke test for docs/portal-api/catalog.yml.

Uses the same Smartschool session as the MCP (HTTPS + cookies + lazy login).
Writes, auth, CSRF, and unmapped XML POSTs are skipped. No response bodies
are printed or written.

    uv run python scripts/smoke_portal.py
    uv run python scripts/smoke_portal.py --legacy-only
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta
from itertools import islice
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CATALOG = REPO_ROOT / "docs" / "portal-api" / "catalog.yml"

SKIP_WRITE = "skip:write"
SKIP_AUTH = "skip:auth"
SKIP_UNMAPPED_XML = "skip:unmapped-xml"
SKIP_UNMAPPED_POST = "skip:unmapped-post"
SKIP_PLACEHOLDER = "skip:placeholder"
HIT_LIBRARY = "hit:library"
HIT_GET = "hit:get"

WRITE_LIBRARIES = frozenset(
    {
        "MarkMessageUnread",
        "AdjustMessageLabel",
        "MessageMoveToTrash",
        "MessageMoveToArchive",
        "MessageComposerForm",
    }
)
WRITE_XML_ACTIONS = frozenset(
    {
        "mark message unread",
        "save msglabel",
        "quick delete",
        "maintenance/save postbox",
    }
)
WRITE_PATH_MARKERS = (
    "/mydoc/api/v1/files/upload",
    "/Upload/Upload/Index",
    "/planner/api/v1/planned-to-dos/",
    "/planner/api/v1/user-settings/save/",
    "/Homepage/Pushwizard/saveactivation",
    "/upload/api/v1/get-upload-directory",
)
AUTH_PATH_MARKERS = (
    "/login",
    "/account-verification",
    "/2fa/",
    "/Topnav/Node/getToken",
)
PLACEHOLDER_RE = re.compile(r"\{([A-Za-z]+)\}")
QUERY_IDS_UNFILLABLE = frozenset(
    {
        "fileID",
        "target",
        "boxType",
        "composeType",
        "msgID",
        "function",
    }
)


@dataclass
class CatalogEndpoint:
    method: str
    path: str
    query_keys: list[str]
    body_keys: list[str]
    module: str
    observed_on: str
    library: str
    mcp_tool: str
    xml_action: str
    notes: str

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> CatalogEndpoint:
        return cls(
            method=str(raw.get("method") or "GET").upper(),
            path=str(raw.get("path") or ""),
            query_keys=list(raw.get("query_keys") or []),
            body_keys=list(raw.get("body_keys") or []),
            module=str(raw.get("module") or ""),
            observed_on=str(raw.get("observed_on") or ""),
            library=str(raw.get("library") or "unmapped"),
            mcp_tool=str(raw.get("mcp_tool") or "none"),
            xml_action=str(raw.get("xml_action") or ""),
            notes=str(raw.get("notes") or "").strip(),
        )

    @property
    def label(self) -> str:
        if self.xml_action:
            return f"{self.path} [{self.xml_action}]"
        return self.path

    @property
    def has_library(self) -> bool:
        return bool(self.library) and self.library != "unmapped"

    @property
    def is_legacy(self) -> bool:
        return self.observed_on == "library"

    @property
    def library_key(self) -> tuple[str, str]:
        if self.xml_action:
            return (self.library, self.xml_action)
        return (self.library, "")


def load_catalog(path: Path | None = None) -> list[CatalogEndpoint]:
    catalog_path = path or DEFAULT_CATALOG
    data = yaml.safe_load(catalog_path.read_text(encoding="utf-8"))
    return [CatalogEndpoint.from_dict(row) for row in data["endpoints"]]


def is_auth(ep: CatalogEndpoint) -> bool:
    if ep.module == "auth":
        return True
    return any(marker in ep.path for marker in AUTH_PATH_MARKERS)


def is_write(ep: CatalogEndpoint) -> bool:
    if ep.method == "DELETE":
        return True
    if ep.library in WRITE_LIBRARIES:
        return True
    if ep.xml_action in WRITE_XML_ACTIONS:
        return True
    if any(marker in ep.path for marker in WRITE_PATH_MARKERS):
        return True
    if ep.method == "POST" and ep.path.rstrip("/") == "/mydoc/api/v1/folders":
        return True
    if ep.method == "POST" and "/mydoc/api/v1/" in ep.path and "{action}" in ep.path:
        return True
    if ep.method == "POST" and ep.path == "/planner/api/v1/ics/profile":
        return True
    if ep.method == "POST" and "jqDispatcher_settings" in ep.path:
        return True
    return ep.method == "POST" and ep.path.endswith("searchUsers")


def is_unmapped_xml(ep: CatalogEndpoint) -> bool:
    if ep.has_library:
        return False
    return "command" in ep.body_keys


def classify(ep: CatalogEndpoint, *, prefer_get: bool = False) -> str:
    """Static decision for a catalog row. Placeholder skips happen at runtime."""
    if is_auth(ep):
        return SKIP_AUTH
    if is_write(ep):
        return SKIP_WRITE
    if is_unmapped_xml(ep):
        return SKIP_UNMAPPED_XML
    if prefer_get and ep.method == "GET":
        return HIT_GET
    if ep.has_library:
        return HIT_LIBRARY
    if ep.method == "GET":
        return HIT_GET
    return SKIP_UNMAPPED_POST


def load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        if key and key not in os.environ:
            os.environ[key] = value


@dataclass
class SmokeResult:
    phase: int
    method: str
    label: str
    library: str
    outcome: str
    http: str = "-"
    items: str = "-"
    note: str = ""


@dataclass
class SmokeContext:
    session: Any
    lessons: list[Any] = field(default_factory=list)
    messages: list[Any] = field(default_factory=list)
    reports: list[Any] = field(default_factory=list)
    results: list[Any] = field(default_factory=list)
    mydocs_folders: list[Any] = field(default_factory=list)
    mydocs_files: list[Any] = field(default_factory=list)
    invoked_library: set[tuple[str, str]] = field(default_factory=set)


def _count(iterable: Iterable[Any], limit: int = 20) -> tuple[int, list[Any]]:
    items = list(islice(iterable, limit))
    return len(items), items


def _exc_note(exc: BaseException) -> str:
    msg = " ".join(str(exc).split())
    if len(msg) > 70:
        msg = msg[:67] + "..."
    name = type(exc).__name__
    return f"{name}: {msg}" if msg else name


def _outcome_from_count(count: int) -> str:
    return "ok" if count else "empty"


def _library_handlers() -> dict[str, Any]:
    import smartschool as _smartschool
    from smartschool import (
        ApplicableAssignmentTypes,
        Attachments,
        CourseList,
        Courses,
        FolderItem,
        FutureTasks,
        Intradesk,
        Message,
        MessageHeaders,
        MyDocs,
        Periods,
        PlannedElements,
        Reports,
        Results,
        SmartschoolHours,
        SmartschoolLessons,
        SmartschoolMomentInfos,
        StudentSupportLinks,
        TopNavCourses,
    )

    pinned_cls = getattr(_smartschool, "PinnedPlannedElements", None)

    def take(name: str):
        def handler(ctx: SmokeContext, ep: CatalogEndpoint) -> tuple[int, list[Any]]:
            classes: dict[str, Any] = {
                "ApplicableAssignmentTypes": ApplicableAssignmentTypes,
                "CourseList": CourseList,
                "Courses": Courses,
                "FutureTasks": FutureTasks,
                "Periods": Periods,
                "PlannedElements": PlannedElements,
                "SmartschoolHours": SmartschoolHours,
                "StudentSupportLinks": StudentSupportLinks,
                "TopNavCourses": TopNavCourses,
            }
            if pinned_cls is not None:
                classes["PinnedPlannedElements"] = pinned_cls
            cls = classes[name]
            return _count(cls(ctx.session))

        return handler

    def lessons(ctx: SmokeContext, ep: CatalogEndpoint) -> tuple[int, list[Any]]:
        n, items = _count(SmartschoolLessons(ctx.session))
        ctx.lessons = items
        return n, items

    def moment_infos(ctx: SmokeContext, ep: CatalogEndpoint) -> tuple[int, list[Any]]:
        if not ctx.lessons:
            _, items = _count(SmartschoolLessons(ctx.session), limit=5)
            ctx.lessons = items
        if not ctx.lessons:
            raise _SkipPlaceholderError("no agenda moment id")
        moment_id = getattr(ctx.lessons[0], "moment_id", None) or getattr(
            ctx.lessons[0], "momentID", None
        )
        if not moment_id:
            raise _SkipPlaceholderError("lesson has no moment_id")
        return _count(SmartschoolMomentInfos(ctx.session, str(moment_id)))

    def message_list(ctx: SmokeContext, ep: CatalogEndpoint) -> tuple[int, list[Any]]:
        n, items = _count(MessageHeaders(ctx.session), limit=10)
        ctx.messages = items
        return n, items

    def message_show(ctx: SmokeContext, ep: CatalogEndpoint) -> tuple[int, list[Any]]:
        if not ctx.messages:
            _, ctx.messages = _count(MessageHeaders(ctx.session), limit=10)
        if not ctx.messages:
            raise _SkipPlaceholderError("no message id")
        msg = Message(ctx.session, ctx.messages[0].id).get()
        return (1, [msg]) if msg is not None else (0, [])

    def attachment_list(
        ctx: SmokeContext, ep: CatalogEndpoint
    ) -> tuple[int, list[Any]]:
        if not ctx.messages:
            _, ctx.messages = _count(MessageHeaders(ctx.session), limit=10)
        candidates = [m for m in ctx.messages if getattr(m, "attachment", 0)]
        if not candidates:
            if not ctx.messages:
                raise _SkipPlaceholderError("no message id")
            candidates = ctx.messages[:1]
        items = list(Attachments(ctx.session, int(candidates[0].id)))
        return len(items), items

    def attachment_download(
        ctx: SmokeContext, ep: CatalogEndpoint
    ) -> tuple[int, list[Any]]:
        _, items = attachment_list(ctx, ep)
        if not items:
            raise _SkipPlaceholderError("no attachment id")
        file_id = getattr(items[0], "file_id", None)
        if file_id is None:
            raise _SkipPlaceholderError("attachment has no file_id")
        resp = ctx.session.get(
            f"/?module=Messages&file=download&fileID={file_id}&target=0"
        )
        if not resp.ok:
            raise _HttpStatusError(resp.status_code)
        return (1 if resp.content else 0, [])

    def reports(ctx: SmokeContext, ep: CatalogEndpoint) -> tuple[int, list[Any]]:
        n, items = _count(Reports(ctx.session))
        ctx.reports = items
        return n, items

    def report_download(
        ctx: SmokeContext, ep: CatalogEndpoint
    ) -> tuple[int, list[Any]]:
        if not ctx.reports:
            _, ctx.reports = _count(Reports(ctx.session))
        if not ctx.reports:
            raise _SkipPlaceholderError("no report download_url")
        payload = ctx.session.json(ctx.reports[0].download_url)
        url = payload.get("url") if isinstance(payload, dict) else None
        if not url:
            raise _SkipPlaceholderError("report payload has no url")
        resp = ctx.session.get(url)
        if not resp.ok:
            raise _HttpStatusError(resp.status_code)
        return (1 if resp.content else 0, [])

    def results(ctx: SmokeContext, ep: CatalogEndpoint) -> tuple[int, list[Any]]:
        n, items = _count(Results(ctx.session), limit=5)
        ctx.results = items
        return n, items

    def result_detail(ctx: SmokeContext, ep: CatalogEndpoint) -> tuple[int, list[Any]]:
        if not ctx.results:
            _, ctx.results = _count(Results(ctx.session), limit=5)
        if not ctx.results:
            raise _SkipPlaceholderError("no evaluationId")
        details = ctx.results[0].details
        return (1, [details]) if details is not None else (0, [])

    def mydocs(ctx: SmokeContext, ep: CatalogEndpoint) -> tuple[int, list[Any]]:
        root = MyDocs(ctx.session)
        items = list(root)
        ctx.mydocs_folders = [i for i in items if i.is_dir()]
        ctx.mydocs_files = [i for i in items if i.is_file()]
        extra = 0
        if ctx.mydocs_folders:
            extra = len(list(ctx.mydocs_folders[0]))
        return len(items) + extra, items

    def mydocs_file(ctx: SmokeContext, ep: CatalogEndpoint) -> tuple[int, list[Any]]:
        if not ctx.mydocs_files:
            mydocs(ctx, ep)
        if not ctx.mydocs_files:
            raise _SkipPlaceholderError("no mydocs fileId")
        data = ctx.mydocs_files[0].download()
        return (1 if data else 0, [])

    def folder_item(ctx: SmokeContext, ep: CatalogEndpoint) -> tuple[int, list[Any]]:
        courses = list(islice(TopNavCourses(ctx.session), 3))
        if not courses:
            raise _SkipPlaceholderError("no courseId")
        folder = FolderItem(
            session=ctx.session, parent=None, course=courses[0], name="(Root)"
        )
        items = folder.items
        return len(items), items

    def intradesk_file(ctx: SmokeContext, ep: CatalogEndpoint) -> tuple[int, list[Any]]:
        from smartschool import IntradeskFile, IntradeskFolder

        root = Intradesk(ctx.session)
        files = [i for i in root if isinstance(i, IntradeskFile)]
        if not files:
            for folder in root:
                if isinstance(folder, IntradeskFolder):
                    files = [i for i in folder if isinstance(i, IntradeskFile)]
                    if files:
                        break
        if not files:
            raise _SkipPlaceholderError("no intradesk fileId")
        data = files[0].download()
        return (1 if data else 0, [])

    def intradesk_folder(
        ctx: SmokeContext, ep: CatalogEndpoint
    ) -> tuple[int, list[Any]]:
        items = list(Intradesk(ctx.session))
        return len(items), items

    handlers: dict[str, Any] = {
        "ApplicableAssignmentTypes": take("ApplicableAssignmentTypes"),
        "CourseList": take("CourseList"),
        "Courses": take("Courses"),
        "FutureTasks": take("FutureTasks"),
        "Periods": take("Periods"),
        "PlannedElements": take("PlannedElements"),
        "SmartschoolHours": take("SmartschoolHours"),
        "StudentSupportLinks": take("StudentSupportLinks"),
        "TopNavCourses": take("TopNavCourses"),
        "SmartschoolLessons": lessons,
        "SmartschoolMomentInfos": moment_infos,
        "MessageHeaders": message_list,
        "Message": message_show,
        "Attachments": attachment_list,
        "Attachment": attachment_download,
        "Reports": reports,
        "Report": report_download,
        "Results": results,
        "Result": result_detail,
        "MyDocsFolder": mydocs,
        "MyDocsFile": mydocs_file,
        "FolderItem": folder_item,
        "IntradeskFile": intradesk_file,
        "IntradeskFolder": intradesk_folder,
    }
    if pinned_cls is not None:
        handlers["PinnedPlannedElements"] = take("PinnedPlannedElements")
    return handlers


class _SkipPlaceholderError(Exception):
    """Runtime skip when a chained id is missing."""


class _HttpStatusError(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(str(status))
        self.status = status


def invoke_library(ctx: SmokeContext, ep: CatalogEndpoint) -> SmokeResult:
    from smartschool import SmartSchoolDownloadError

    handlers = _library_handlers()
    handler = handlers.get(ep.library)
    if handler is None:
        return SmokeResult(
            phase=1,
            method=ep.method,
            label=ep.label,
            library=ep.library,
            outcome="skip:no-handler",
        )
    try:
        count, _items = handler(ctx, ep)
        return SmokeResult(
            phase=1,
            method=ep.method,
            label=ep.label,
            library=ep.library,
            outcome=_outcome_from_count(count),
            items=str(count),
        )
    except _SkipPlaceholderError:
        return SmokeResult(
            phase=1,
            method=ep.method,
            label=ep.label,
            library=ep.library,
            outcome=SKIP_PLACEHOLDER,
            items="-",
        )
    except _HttpStatusError as exc:
        kind = "http_4xx" if 400 <= exc.status < 500 else "error"
        return SmokeResult(
            phase=1,
            method=ep.method,
            label=ep.label,
            library=ep.library,
            outcome=kind,
            http=str(exc.status),
        )
    except SmartSchoolDownloadError as exc:
        status = int(getattr(exc, "status_code", 0) or 0)
        kind = "http_4xx" if 400 <= status < 500 else "error"
        return SmokeResult(
            phase=1,
            method=ep.method,
            label=ep.label,
            library=ep.library,
            outcome=kind,
            http=str(status) if status else "-",
            note=_exc_note(exc),
        )
    except Exception as exc:
        return SmokeResult(
            phase=1,
            method=ep.method,
            label=ep.label,
            library=ep.library,
            outcome="error",
            note=_exc_note(exc),
        )


def fill_path(path: str, ctx: SmokeContext) -> tuple[str, list[str]]:
    mapping: dict[str, str] = {}
    try:
        user = ctx.session.authenticated_user or {}
        if user.get("id") is not None:
            mapping["userId"] = str(user["id"])
    except Exception:
        pass
    try:
        mapping["platformId"] = str(ctx.session.platform_id)
    except Exception:
        pass

    missing: list[str] = []

    def repl(match: re.Match[str]) -> str:
        key = match.group(1)
        value = mapping.get(key)
        if not value:
            missing.append(key)
            return match.group(0)
        return value

    return PLACEHOLDER_RE.sub(repl, path), missing


def get_query(ep: CatalogEndpoint) -> dict[str, Any] | None:
    keys = set(ep.query_keys)
    if keys & QUERY_IDS_UNFILLABLE:
        return None
    params: dict[str, Any] = {}
    today = date.today()
    if "from" in keys:
        params["from"] = today.isoformat()
    if "to" in keys:
        params["to"] = (today + timedelta(days=7)).isoformat()
    if "pageNumber" in keys:
        params["pageNumber"] = 1
    if "itemsOnPage" in keys:
        params["itemsOnPage"] = 5
    return params


def _json_item_count(payload: Any) -> int:
    if payload is None or payload == "" or payload == {}:
        return 0
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict):
        return 1
    return 1


def invoke_get(ctx: SmokeContext, ep: CatalogEndpoint, phase: int) -> SmokeResult:
    filled, missing = fill_path(ep.path, ctx)
    if missing or "{" in filled:
        return SmokeResult(
            phase=phase,
            method=ep.method,
            label=ep.label,
            library=ep.library,
            outcome=SKIP_PLACEHOLDER,
        )
    params = get_query(ep)
    if params is None:
        return SmokeResult(
            phase=phase,
            method=ep.method,
            label=ep.label,
            library=ep.library,
            outcome=SKIP_PLACEHOLDER,
        )
    try:
        resp = ctx.session.get(filled, params=params or None)
    except Exception as exc:
        return SmokeResult(
            phase=phase,
            method=ep.method,
            label=ep.label,
            library=ep.library,
            outcome="error",
            note=_exc_note(exc),
        )
    status = resp.status_code
    if status >= 400:
        kind = "http_4xx" if status < 500 else "error"
        return SmokeResult(
            phase=phase,
            method=ep.method,
            label=ep.label,
            library=ep.library,
            outcome=kind,
            http=str(status),
        )
    count = 1
    ctype = (resp.headers.get("content-type") or "").lower()
    if "json" in ctype:
        try:
            count = _json_item_count(resp.json())
        except Exception:
            count = 1 if resp.content else 0
    elif not resp.content:
        count = 0
    return SmokeResult(
        phase=phase,
        method=ep.method,
        label=ep.label,
        library=ep.library,
        outcome=_outcome_from_count(count),
        http=str(status),
        items=str(count),
    )


def skipped_result(ep: CatalogEndpoint, phase: int, outcome: str) -> SmokeResult:
    return SmokeResult(
        phase=phase,
        method=ep.method,
        label=ep.label,
        library=ep.library,
        outcome=outcome,
    )


def run_row(
    ep: CatalogEndpoint,
    ctx: SmokeContext,
    phase: int,
    *,
    prefer_get: bool,
) -> SmokeResult:
    decision = classify(ep, prefer_get=prefer_get)
    if decision.startswith("skip:"):
        return skipped_result(ep, phase, decision)
    if decision == HIT_LIBRARY:
        if ep.library_key in ctx.invoked_library:
            return skipped_result(ep, phase, "skip:duplicate")
        ctx.invoked_library.add(ep.library_key)
        result = invoke_library(ctx, ep)
        result.phase = phase
        return result
    return invoke_get(ctx, ep, phase)


def run_smoke(
    endpoints: list[CatalogEndpoint],
    ctx: SmokeContext,
    *,
    legacy_only: bool,
) -> list[SmokeResult]:
    results: list[SmokeResult] = []
    for ep in endpoints:
        if ep.is_legacy:
            results.append(run_row(ep, ctx, phase=1, prefer_get=False))
    if legacy_only:
        return results
    for ep in endpoints:
        if not ep.is_legacy:
            results.append(run_row(ep, ctx, phase=2, prefer_get=True))
    return results


def _cell(value: object, width: int) -> str:
    text = str(value)
    if len(text) > width:
        text = text[: width - 1] + "…"
    return f"{text:<{width}}"


def format_table(rows: list[SmokeResult]) -> str:
    header = (
        f"{_cell('PH', 3)} {_cell('METHOD', 7)} {_cell('OUTCOME', 16)} "
        f"{_cell('HTTP', 4)} {_cell('ITEMS', 5)} {_cell('LIBRARY', 28)} PATH"
    )
    lines = [header, "-" * 100]
    for row in rows:
        label = row.label
        if row.note:
            label = f"{label}  [{row.note}]"
        if len(label) > 90:
            label = label[:87] + "..."
        lines.append(
            f"{_cell(row.phase, 3)} {_cell(row.method, 7)} "
            f"{_cell(row.outcome, 16)} {_cell(row.http, 4)} "
            f"{_cell(row.items, 5)} {_cell(row.library, 28)} {label}"
        )
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.outcome] = counts.get(row.outcome, 0) + 1
    summary = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
    lines.append("")
    lines.append(f"{len(rows)} rows: {summary}")
    return "\n".join(lines)


def missing_credentials() -> bool:
    return not (
        os.environ.get("SMARTSCHOOL_USERNAME")
        and os.environ.get("SMARTSCHOOL_PASSWORD")
        and os.environ.get("SMARTSCHOOL_MAIN_URL")
    )


def login(ctx_session_factory: Any | None = None) -> Any:
    from smartschool import EnvCredentials, Smartschool

    factory = ctx_session_factory or (lambda: Smartschool(EnvCredentials()))
    session = factory()
    session.ensure_authenticated()
    return session


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--legacy-only",
        action="store_true",
        help="Only smoke observed_on: library rows (phase 1).",
    )
    parser.add_argument(
        "--catalog",
        type=Path,
        default=DEFAULT_CATALOG,
        help="Path to catalog.yml",
    )
    args = parser.parse_args(argv)

    from smartschool_mcp.credentials import activate_saved_credentials

    activate_saved_credentials()
    if missing_credentials():
        print(
            "Missing SMARTSCHOOL_USERNAME / PASSWORD / MAIN_URL "
            "(shared ~/.config/smartschool/credentials.json, or the environment).",
            file=sys.stderr,
        )
        return 2

    endpoints = load_catalog(args.catalog)
    try:
        session = login()
    except Exception as exc:
        print(f"Login failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    ctx = SmokeContext(session=session)
    rows = run_smoke(endpoints, ctx, legacy_only=args.legacy_only)
    print(format_table(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
