#!/usr/bin/env python3
"""Cijfers: grades from Results. Read-only."""

from __future__ import annotations

import argparse
from typing import Any

from smartschool import Results

from _common import format_date, main, open_session


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read Smartschool grades (cijfers).")
    parser.add_argument("--limit", type=int, default=15)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument(
        "--course",
        default=None,
        help="Case-insensitive substring of the course name.",
    )
    parser.add_argument(
        "--no-details",
        action="store_true",
        help="Skip per-grade average/median lookups.",
    )
    return parser.parse_args(argv)


def _graphic_pair(graphic: object) -> dict[str, Any]:
    return {
        "description": getattr(graphic, "description", "N/A"),
        "value": getattr(graphic, "value", None),
    }


def result_row(result: object, *, include_details: bool) -> dict[str, Any]:
    courses = getattr(result, "courses", None) or []
    course_name = getattr(courses[0], "name", "Unknown") if courses else "Unknown"
    owner = getattr(result, "gradebook_owner", None)
    owner_name = getattr(owner, "name", None)
    period = getattr(result, "period", None)
    graphic = getattr(result, "graphic", None)
    feedback = getattr(result, "feedback", None) or []
    feedback_text = ""
    if feedback:
        feedback_text = str(getattr(feedback[0], "text", "") or "")

    row: dict[str, Any] = {
        "course": course_name,
        "assignment": getattr(result, "name", None) or "Unknown Assignment",
        "teacher": getattr(owner_name, "starting_with_first_name", None),
        "period": getattr(period, "name", None) if period else None,
        "score_description": getattr(graphic, "description", "N/A"),
        "score_value": getattr(graphic, "value", None),
        "achieved_points": getattr(graphic, "achieved_points", None),
        "total_points": getattr(graphic, "total_points", None),
        "percentage": getattr(graphic, "percentage", None),
        "date": format_date(getattr(result, "date", None)),
        "published_date": format_date(getattr(result, "availability_date", None)),
        "counts": getattr(result, "does_count", None),
        "feedback": feedback_text,
    }
    if not include_details:
        return row

    row["average"] = None
    row["median"] = None
    try:
        detail = result.details  # type: ignore[attr-defined]
        tendencies = getattr(detail, "central_tendencies", None) or []
        if tendencies and hasattr(tendencies[0], "graphic"):
            row["average"] = _graphic_pair(tendencies[0].graphic)
        if len(tendencies) > 1 and hasattr(tendencies[1], "graphic"):
            row["median"] = _graphic_pair(tendencies[1].graphic)
    except Exception:
        pass
    return row


def build(argv: list[str] | None = None) -> dict[str, Any]:
    args = parse_args(argv)
    if args.limit < 0 or args.offset < 0:
        return {"error": "limit and offset must be >= 0"}

    include_details = not args.no_details
    all_results = list(Results(open_session()))
    if args.course:
        needle = args.course.lower()
        filtered = []
        for result in all_results:
            courses = getattr(result, "courses", None) or []
            course_name = getattr(courses[0], "name", "") if courses else ""
            if needle in str(course_name).lower():
                filtered.append(result)
        all_results = filtered

    page = all_results[args.offset : args.offset + args.limit]
    rows = [result_row(result, include_details=include_details) for result in page]
    end_index = args.offset + args.limit
    return {
        "results": rows,
        "pagination": {
            "limit": args.limit,
            "offset": args.offset,
            "total": len(all_results),
            "returned": len(rows),
            "has_more": end_index < len(all_results),
        },
        "filters": {
            "course_filter": args.course,
            "include_details": include_details,
        },
    }


if __name__ == "__main__":
    main(build)
