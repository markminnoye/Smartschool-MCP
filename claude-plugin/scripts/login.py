#!/usr/bin/env python3
"""Log in with the single configured account and print who that session is."""

from __future__ import annotations

import argparse
from typing import Any

from _common import main, open_session, public_user


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Log in with the single configured Smartschool account."
    )
    return parser.parse_args(argv)


def build(argv: list[str] | None = None) -> dict[str, Any]:
    parse_args(argv)
    session = open_session()
    creds = getattr(session, "creds", None)
    host = str(getattr(creds, "main_url", "") or "")
    user = session.confirm_login()
    return {
        "ok": True,
        "main_url": host,
        "user": public_user(user),
    }


if __name__ == "__main__":
    main(build)
