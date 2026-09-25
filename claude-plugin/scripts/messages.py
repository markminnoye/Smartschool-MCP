#!/usr/bin/env python3
"""Berichten: list or read messages. Read-only; nothing is sent or moved."""

from __future__ import annotations

import argparse
from typing import Any

from smartschool import BoxType, Message, MessageHeaders

from _common import format_date, main, open_session

_BOXES = ("INBOX", "SENT", "DRAFT", "SCHEDULED", "TRASH")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read Smartschool messages.")
    parser.add_argument(
        "--box",
        default="INBOX",
        help="INBOX, SENT, DRAFT, SCHEDULED, or TRASH.",
    )
    parser.add_argument("--limit", type=int, default=15)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--search", default=None, help="Match subject, then body.")
    parser.add_argument("--sender", default=None, help="Partial sender match.")
    parser.add_argument(
        "--body",
        action="store_true",
        help="Include the message body for each returned row.",
    )
    parser.add_argument(
        "--id",
        dest="message_id",
        type=int,
        default=None,
        help="Fetch one message by id (implies body).",
    )
    return parser.parse_args(argv)


def _check_page(limit: int, offset: int) -> str | None:
    if limit < 0 or offset < 0:
        return "limit and offset must be >= 0"
    return None


def message_row(header: object, *, body: str | None = None) -> dict[str, Any]:
    attachment_count = (
        getattr(header, "attachments", None) or getattr(header, "attachment", 0) or 0
    )
    try:
        attachment_count = int(attachment_count)
    except (TypeError, ValueError):
        attachment_count = 0
    row: dict[str, Any] = {
        "id": getattr(header, "id", None),
        "from": getattr(header, "from_", None) or "Unknown Sender",
        "subject": getattr(header, "subject", None) or "No Subject",
        "date": format_date(getattr(header, "date", None)),
        "unread": getattr(header, "unread", None),
        "priority": getattr(header, "priority", None),
        "has_attachments": attachment_count > 0,
        "attachment_count": attachment_count,
    }
    if body is not None:
        row["body"] = body
    return row


def _body_text(session: object, header: object) -> str:
    cached = getattr(header, "_cached_message", None)
    full = cached or Message(session, header.id).get()  # type: ignore[arg-type]
    return str(getattr(full, "body", "") or "")


def filter_headers(
    headers: list[Any],
    *,
    sender: str | None,
    search: str | None,
    session: object,
) -> list[Any]:
    selected = list(headers)
    if sender:
        needle = sender.lower()
        selected = [
            header
            for header in selected
            if needle in str(getattr(header, "from_", "") or "").lower()
        ]
    if not search:
        return selected

    matched: list[Any] = []
    needle = search.lower()
    for header in selected:
        subject = str(getattr(header, "subject", "") or "").lower()
        if needle in subject:
            matched.append(header)
            continue
        try:
            body = _body_text(session, header).lower()
        except Exception:
            continue
        if needle in body:
            matched.append(header)
    return matched


def build(argv: list[str] | None = None) -> dict[str, Any]:
    args = parse_args(argv)
    page_error = _check_page(args.limit, args.offset)
    if page_error:
        return {"error": page_error}

    box_name = str(args.box).upper()
    if box_name not in _BOXES:
        return {"error": f"Unknown box {args.box!r}. Use {', '.join(_BOXES)}."}
    box = getattr(BoxType, box_name)

    session = open_session()
    if args.message_id is not None:
        full = Message(session, args.message_id).get()
        return message_row(full, body=str(getattr(full, "body", "") or ""))

    headers = filter_headers(
        list(MessageHeaders(session, box_type=box)),
        sender=args.sender,
        search=args.search,
        session=session,
    )
    page = headers[args.offset : args.offset + args.limit]
    rows = []
    for header in page:
        body = _body_text(session, header) if args.body else None
        rows.append(message_row(header, body=body))

    end_index = args.offset + args.limit
    return {
        "messages": rows,
        "pagination": {
            "limit": args.limit,
            "offset": args.offset,
            "total": len(headers),
            "returned": len(rows),
            "has_more": end_index < len(headers),
        },
        "filters": {
            "box_type": box_name,
            "search_query": args.search,
            "sender_filter": args.sender,
            "include_body": args.body,
        },
    }


if __name__ == "__main__":
    main(build)
