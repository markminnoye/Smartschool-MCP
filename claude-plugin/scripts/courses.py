#!/usr/bin/env python3
"""List courses and teachers for the configured account."""

from __future__ import annotations

from typing import Any

from smartschool import Courses

from _common import main, open_session, teacher_names


def build() -> dict[str, Any]:
    rows = []
    for course in Courses(open_session()):
        rows.append(
            {
                "name": getattr(course, "name", "") or "",
                "teachers": teacher_names(getattr(course, "teachers", None)),
            }
        )
    return {"courses": rows, "total": len(rows)}


if __name__ == "__main__":
    main(build)
