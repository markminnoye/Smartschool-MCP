"""Smartschool settings are checked before anything is sent to Smartschool.

A typo in the school address or the birth date used to cost a real login
attempt; Smartschool locks an account after a few failed ones.
"""

from __future__ import annotations

import pytest

from smartschool_mcp import settings
from smartschool_mcp.settings import SettingsError

# -- school address ----------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "myschool.smartschool.be",
        " myschool.smartschool.be ",
        "MySchool.Smartschool.BE",
        "myschool. smartschool.be",
        "https://myschool.smartschool.be",
        "https://myschool.smartschool.be/login",
        "myschool.smartschool.be/",
        "myschool",
    ],
)
def test_school_address_is_tidied_to_a_host(raw: str) -> None:
    assert settings.school_host(raw) == "myschool.smartschool.be"


def test_school_address_keeps_a_port() -> None:
    assert settings.school_host("school.example.be:8443") == "school.example.be:8443"


@pytest.mark.parametrize("raw", ["", "   ", "my school!", "http://", "-bad-.be"])
def test_unusable_school_address_is_refused(raw: str) -> None:
    with pytest.raises(SettingsError, match="SMARTSCHOOL_MAIN_URL"):
        settings.school_host(raw)


# -- verification answer -----------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2012-03-04", "2012-03-04"),
        (" 2012-03-04 ", "2012-03-04"),
        ("04/03/2012", "2012-03-04"),
        ("4-3-2012", "2012-03-04"),
        ("04.03.2012", "2012-03-04"),
        ("12-11-2014", "2014-11-12"),
        ("12/11/2014", "2014-11-12"),
    ],
)
def test_birth_date_becomes_iso(raw: str, expected: str) -> None:
    assert settings.verification_answer(raw) == expected


def test_totp_secret_is_kept_without_spaces() -> None:
    assert settings.verification_answer("jbsw y3dp ehpk 3pxp") == "JBSWY3DPEHPK3PXP"


@pytest.mark.parametrize(
    "raw", ["", "2012-02-30", "31/13/2012", "march 4th", "12345", "2012/03"]
)
def test_unusable_verification_answer_is_refused(raw: str) -> None:
    with pytest.raises(SettingsError, match="SMARTSCHOOL_MFA") as excinfo:
        settings.verification_answer(raw)

    if raw:
        assert raw not in str(excinfo.value)  # the answer is a secret


# -- environment -------------------------------------------------------------


def test_env_credentials_are_tidied(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMARTSCHOOL_USERNAME", " john.doe ")
    monkeypatch.setenv("SMARTSCHOOL_PASSWORD", "s3cret")
    monkeypatch.setenv("SMARTSCHOOL_MAIN_URL", "https://myschool. smartschool.be/")
    monkeypatch.setenv("SMARTSCHOOL_MFA", "04/03/2012")

    creds = settings.env_credentials()

    assert creds.username == "john.doe"
    assert creds.password == "s3cret"
    assert creds.main_url == "myschool.smartschool.be"
    assert creds.mfa == "2012-03-04"


def test_missing_env_settings_are_named(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMARTSCHOOL_USERNAME", "john.doe")
    monkeypatch.setenv("SMARTSCHOOL_PASSWORD", "")
    monkeypatch.setenv("SMARTSCHOOL_MAIN_URL", "myschool.smartschool.be")
    monkeypatch.delenv("SMARTSCHOOL_MFA", raising=False)

    with pytest.raises(SettingsError) as excinfo:
        settings.env_credentials()

    message = str(excinfo.value)
    assert "SMARTSCHOOL_PASSWORD" in message
    assert "SMARTSCHOOL_MFA" in message
    assert "SMARTSCHOOL_USERNAME" not in message
