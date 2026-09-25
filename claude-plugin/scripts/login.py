#!/usr/bin/env python3
"""Log in with the single configured account and print who that session is."""

from __future__ import annotations

from typing import Any

from _common import main, open_session, public_user


def build() -> dict[str, Any]:
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
