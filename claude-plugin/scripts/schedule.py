#!/usr/bin/env python3
"""Agenda: Planner calendar for one day or a date range.

Mirrors ``get_schedule`` (one day, no types filter) and ``get_planned_elements``
(optional range, types, includes). Does not call the removed Schoolagenda XML.
"""

from __future__ import annotations

import argparse
from datetime import date, timedelta
from typing import Any

from smartschool import PlannedElements

from _common import csv_or_none, main, open_session, planned_element


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected YYYY-MM-DD, got {value!r}") from exc


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read the Smartschool planner calendar (agenda)."
    )
    parser.add_argument("--date", type=_parse_date, help="Single day YYYY-MM-DD.")
    parser.add_argument(
        "--offset",
        type=int,
        default=None,
        help="Days from today (0 = today). Ignored when --date or --from is set.",
    )
    parser.add_argument(
        "--from",
        dest="from_date",
        type=_parse_date,
        help="Inclusive range start YYYY-MM-DD.",
    )
    parser.add_argument(
        "--to",
        dest="to_date",
        type=_parse_date,
        help="Inclusive range end YYYY-MM-DD.",
    )
    parser.add_argument(
        "--days-ahead",
        type=int,
        default=None,
        help="End = start + N days when --to is omitted.",
    )
    parser.add_argument(
        "--types",
        default=None,
        help="Optional comma-separated planned-* filter. Omit for the full calendar.",
    )
    parser.add_argument(
        "--includes",
        default=None,
        help="Optional comma-separated expansions (icon,courses,locations,…).",
    )
    return parser.parse_args(argv)


def resolve_range(
    args: argparse.Namespace, today: date | None = None
) -> tuple[date, date]:
    today = date.today() if today is None else today
    if args.days_ahead is not None and args.days_ahead < 0:
        raise ValueError("--days-ahead must be >= 0")
    if args.from_date is not None and args.date is not None:
        raise ValueError("pass only one of --date and --from")

    if args.from_date is not None:
        start = args.from_date
    elif args.date is not None:
        start = args.date
    elif args.offset is not None:
        start = today + timedelta(days=args.offset)
    else:
        start = today

    if args.to_date is not None:
        end = args.to_date
    elif args.days_ahead is not None:
        end = start + timedelta(days=args.days_ahead)
    else:
        end = start

    if end < start:
        raise ValueError(
            f"end date {end.isoformat()} is before start {start.isoformat()}"
        )
    return start, end


def build(argv: list[str] | None = None) -> dict[str, Any]:
    args = parse_args(argv)
    start, end = resolve_range(args)
    elements = [
        planned_element(element)
        for element in PlannedElements(
            open_session(),
            from_date=start,
            till_date=end,
            types=csv_or_none(args.types),
            includes=csv_or_none(args.includes),
        )
    ]
    payload: dict[str, Any] = {
        "period": {"from": start.isoformat(), "to": end.isoformat()},
        "elements": elements,
        "total": len(elements),
    }
    if start == end:
        payload["date"] = start.isoformat()
    return payload


if __name__ == "__main__":
    main(build)
