"""Check and tidy the Smartschool settings before anything is sent.

A typo in the school address or the verification answer used to cost a real
login attempt, and Smartschool locks an account after a few failed ones. These
checks run first and say which setting to fix. They never echo the password or
the verification answer.
"""

from __future__ import annotations

import os
import re
from datetime import date
from urllib.parse import urlsplit

from smartschool import AppCredentials

SCHOOL_SUFFIX = ".smartschool.be"

_LABEL_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
_ISO_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
# Belgian notation puts the day first: 4/3/2012, 04-03-2012, 04.03.2012.
_DAY_FIRST_RE = re.compile(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})")
_TOTP_SECRET_RE = re.compile(r"[A-Z2-7]{16,}=*")


class SettingsError(ValueError):
    """A Smartschool setting cannot be used; nothing was sent."""


def school_host(raw: str) -> str:
    """``SMARTSCHOOL_MAIN_URL`` as a bare host (``myschool.smartschool.be``).

    Spaces, a scheme and a path are dropped; a bare subdomain such as
    ``myschool`` gets ``.smartschool.be`` added.
    """
    text = re.sub(r"\s+", "", raw).lower()
    if "://" not in text:
        text = f"https://{text}"
    try:
        parts = urlsplit(text)
        host = (parts.hostname or "").strip(".")
        port = parts.port
    except ValueError:
        host, port = "", None
    if host and "." not in host:
        host += SCHOOL_SUFFIX
    labels = host.split(".")
    if len(labels) < 2 or not all(_LABEL_RE.fullmatch(label) for label in labels):
        raise SettingsError(
            "SMARTSCHOOL_MAIN_URL must be your school's Smartschool address, "
            f"e.g. myschool{SCHOOL_SUFFIX} (got {raw!r})"
        )
    return f"{host}:{port}" if port else host


def verification_answer(raw: str) -> str:
    """``SMARTSCHOOL_MFA`` as Smartschool expects it.

    A birth date becomes ``YYYY-MM-DD`` (day-first ``04/03/2012`` is accepted);
    a 2FA (TOTP) secret is kept, without spaces, in capitals.
    """
    text = raw.strip()
    iso = _ISO_DATE_RE.fullmatch(text)
    day_first = _DAY_FIRST_RE.fullmatch(text)
    if iso or day_first:
        if iso:
            year, month, day = (int(part) for part in iso.groups())
        else:
            assert day_first is not None
            day, month, year = (int(part) for part in day_first.groups())
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            pass
    else:
        secret = re.sub(r"\s+", "", text).upper()
        if _TOTP_SECRET_RE.fullmatch(secret):
            return secret
    raise SettingsError(
        "SMARTSCHOOL_MFA must be the birth date Smartschool asks for, as "
        "dd-mm-yyyy (or YYYY-MM-DD), or a 2FA (TOTP) secret"
    )


def env_credentials() -> AppCredentials:
    """Credentials from the ``SMARTSCHOOL_*`` variables, checked and tidied."""
    names = (
        "SMARTSCHOOL_USERNAME",
        "SMARTSCHOOL_PASSWORD",
        "SMARTSCHOOL_MAIN_URL",
        "SMARTSCHOOL_MFA",
    )
    values = {name: os.environ.get(name, "") for name in names}
    missing = [name for name, value in values.items() if not value.strip()]
    if missing:
        raise SettingsError(f"Missing Smartschool settings: {', '.join(missing)}")
    return AppCredentials(
        username=values["SMARTSCHOOL_USERNAME"].strip(),
        password=values["SMARTSCHOOL_PASSWORD"],
        main_url=school_host(values["SMARTSCHOOL_MAIN_URL"]),
        mfa=verification_answer(values["SMARTSCHOOL_MFA"]),
    )
