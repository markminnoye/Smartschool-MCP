"""Shared Smartschool account store for the MCP server and local plugins.

One file, ``credentials.json``, holds every school login. ``GROK_PLUGIN_DATA``
selects the directory when it is set; otherwise the file is
``~/.config/smartschool/credentials.json``. On macOS the password and birth
date can live in the Keychain for that same file. Process environment
variables override one run. Legacy ``.env`` and ``config.env`` files only seed
an empty store.

This module does not contact Smartschool.
"""

from __future__ import annotations

import contextlib
import getpass
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from smartschool import SmartSchoolAuthenticationError

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PLUGIN_CONFIG = _REPO_ROOT / "claude-plugin" / "config.env"
CREDENTIAL_KEYS = (
    "SMARTSCHOOL_MAIN_URL",
    "SMARTSCHOOL_USERNAME",
    "SMARTSCHOOL_PASSWORD",
    "SMARTSCHOOL_MFA",
)
_LEGACY_PLACEHOLDERS = {
    "your_username",
    "your_password",
    "your-school.smartschool.be",
    "YYYY-MM-DD",
}


class CredentialMixError(RuntimeError):
    """Env and config.env disagree. No login is attempted."""


class MissingCredentialsError(RuntimeError):
    """No usable credentials. No login is attempted."""


class CredentialStoreError(RuntimeError):
    """The credential file or keychain could not be used. No login is attempted."""


_NO_CREDENTIALS = (
    "Geen Smartschool-gegevens gevonden. Zet SMARTSCHOOL_USER en "
    "SMARTSCHOOL_PASSWORD (plus SMARTSCHOOL_MAIN_URL en SMARTSCHOOL_MFA), "
    "of start in een terminal om ze eenmalig op te slaan. "
    "Geen loginpoging gedaan."
)
NO_DESKTOP_CREDENTIALS = (
    "Er zijn nog geen Smartschool-gegevens ingesteld. Open in Claude Desktop "
    "Instellingen > Extensies > Smartschool > Configure en vul school, "
    "gebruikersnaam, wachtwoord, geboortedatum en naam van je kind in. "
    "Sluit daarna Claude helemaal af en open het opnieuw."
)
INCOMPLETE_DESKTOP_CREDENTIALS = (
    "De Smartschool-gegevens zijn onvolledig. Open in Claude Desktop "
    "Instellingen > Extensies > Smartschool > Configure en vul school, "
    "gebruikersnaam, wachtwoord, geboortedatum (jjjj-mm-dd) en naam van je kind in. "
    "Sluit daarna Claude helemaal af en open het opnieuw. Geen loginpoging gedaan."
)
_KEYCHAIN_SERVICE = "smartschool-mcp"
_KEYCHAIN_MFA_SERVICE = "smartschool-mcp-mfa"
_SECRET_ENV_KEYS = ("SMARTSCHOOL_PASSWORD", "SMARTSCHOOL_MFA")
_SCHOOL_SUFFIX = ".smartschool.be"


def parse_env_file(text: str) -> dict[str, str]:
    """Parse a dotenv-style file. Values are not shell-expanded."""
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def _configured_file(path: Path | None = None) -> Path | None:
    """Return the Claude ``config.env`` path, if one is selected and present."""
    if path is not None:
        if not path.is_file():
            raise FileNotFoundError(f"Credential file not found: {path}")
        return path
    explicit = os.environ.get("SMARTSCHOOL_CONFIG", "").strip()
    if explicit:
        chosen = Path(explicit).expanduser()
        if not chosen.is_file():
            raise FileNotFoundError(f"Credential file not found: {chosen}")
        return chosen
    if _PLUGIN_CONFIG.is_file():
        return _PLUGIN_CONFIG
    return None


def load_config(path: Path | None = None) -> Path | None:
    """Load KEY=VALUE config into the environment.

    Existing environment variables win, so a shell export is not overwritten.
    ``SMARTSCHOOL_CONFIG`` selects a file explicitly. Otherwise
    ``<plugin>/config.env`` is used when it exists.
    """
    chosen = _configured_file(path)
    if chosen is None:
        return None

    parsed = parse_env_file(chosen.read_text(encoding="utf-8"))
    _apply_credentials(parsed)
    for key, value in parsed.items():
        if key not in CREDENTIAL_KEYS:
            os.environ.setdefault(key, value)
    return chosen


def _credential_map(source: Mapping[str, str]) -> dict[str, str]:
    return {key: source[key] for key in CREDENTIAL_KEYS if key in source}


def _apply_credentials(file_values: Mapping[str, str]) -> None:
    """Refuse when env and config.env would mix two accounts. No network."""
    from_file = _credential_map(file_values)
    from_env = _credential_map(os.environ)
    if from_file and from_env and from_file != from_env:
        raise CredentialMixError(
            "LOGIN STOPPED, geen poging gedaan. config.env en de environment "
            "geven verschillende Smartschool-gegevens. Gebruik één bron en "
            "wis de andere. Niet opnieuw proberen met een mix."
        )
    if from_file:
        for key in CREDENTIAL_KEYS:
            if key in from_file:
                os.environ[key] = from_file[key]
            else:
                os.environ.pop(key, None)


def credentials_path() -> Path:
    """Shared account file for every local Smartschool client.

    ``GROK_PLUGIN_DATA`` wins when it is set. Otherwise
    ``~/.config/smartschool/credentials.json``.
    """
    root = os.environ.get("GROK_PLUGIN_DATA", "").strip()
    if root:
        return Path(root).expanduser() / "credentials.json"
    return Path.home() / ".config" / "smartschool" / "credentials.json"


def keychain_enabled() -> bool:
    """macOS Keychain via ``security``, unless ``SMARTSCHOOL_KEYCHAIN`` is off."""
    if sys.platform != "darwin":
        return False
    flag = os.environ.get("SMARTSCHOOL_KEYCHAIN", "1").strip().lower()
    if flag in {"0", "false", "no", "off"}:
        return False
    return shutil.which("security") is not None


def _keychain_account(username: str, host: str) -> str:
    return f"{username}@{host}"


def _run_security(args: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            args,
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise CredentialStoreError(
            "macOS Sleutelhanger is niet bereikbaar. Geen loginpoging gedaan."
        ) from None


def keychain_set(service: str, account: str, secret: str) -> None:
    """Store one secret with ``security``. The secret is not logged."""
    completed = _run_security(
        [
            "security",
            "add-generic-password",
            "-U",
            "-s",
            service,
            "-a",
            account,
            "-w",
            secret,
        ]
    )
    if completed.returncode != 0:
        raise CredentialStoreError(
            "macOS Sleutelhanger kon het geheim niet bewaren. Geen loginpoging gedaan."
        )


def keychain_get(service: str, account: str) -> str | None:
    """Read one secret. ``None`` when the item is missing. Nothing is logged."""
    completed = _run_security(
        ["security", "find-generic-password", "-s", service, "-a", account, "-w"]
    )
    if completed.returncode != 0:
        return None
    value = completed.stdout.removesuffix("\n")
    return value or None


def keychain_delete(service: str, account: str) -> None:
    """Remove one keychain item. A missing item is not an error."""
    _run_security(["security", "delete-generic-password", "-s", service, "-a", account])


def _write_private(path: Path, text: str) -> None:
    """Write ``text`` so the directory is 0700 and the file is 0600."""
    directory = path.parent
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    temporary = directory / f".{path.name}.tmp"
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, text.encode("utf-8"))
    finally:
        os.close(fd)
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
    os.chmod(path, 0o600)


def _check_username(username: str) -> str:
    if (
        not username
        or username in {".", ".."}
        or "/" in username
        or "\\" in username
        or "\n" in username
        or "\r" in username
    ):
        raise CredentialStoreError("Ongeldige gebruikersnaam. Geen loginpoging gedaan.")
    return username


def _reject_newlines(*values: str) -> None:
    for value in values:
        if "\n" in value or "\r" in value:
            raise CredentialStoreError(
                "Gegevens mogen geen nieuwe regel bevatten. "
                "Niets opgeslagen. Geen loginpoging gedaan."
            )


def _label_ok(label: str) -> bool:
    return bool(re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", label))


def normalize_school(value: str) -> str:
    """Build ``<subdomain>.smartschool.be`` from a subdomain, host, or https URL."""
    raw = value.strip()
    if not raw:
        raise SmartSchoolAuthenticationError(
            "School ontbreekt. Geef het subdomein, bijvoorbeeld dering of depass. "
            "Geen loginpoging gedaan."
        )
    if "://" in raw:
        parsed = urlparse(raw)
        if parsed.scheme != "https":
            raise SmartSchoolAuthenticationError("SMARTSCHOOL_MAIN_URL moet https zijn")
        host = (parsed.hostname or "").lower()
    else:
        host = raw.split("/")[0].split(":")[0].lower().strip(".")
    if host.endswith(_SCHOOL_SUFFIX):
        label = host[: -len(_SCHOOL_SUFFIX)]
    elif "." not in host:
        label = host
    else:
        raise SmartSchoolAuthenticationError(
            "School moet een subdomein zijn, bijvoorbeeld dering of depass. "
            "Geen loginpoging gedaan."
        )
    if not _label_ok(label):
        raise SmartSchoolAuthenticationError(
            "School moet een subdomein zijn, bijvoorbeeld dering of depass. "
            "Geen loginpoging gedaan."
        )
    return f"{label}{_SCHOOL_SUFFIX}"


def school_host(main_url: str) -> str:
    """Host of a Smartschool school. Accepts a subdomain, host, or https URL."""
    return normalize_school(main_url)


def subdomain_of(main_url: str) -> str:
    return normalize_school(main_url)[: -len(_SCHOOL_SUFFIX)]


def profile_id(main_url: str, username: str) -> str:
    return f"{subdomain_of(main_url)}:{username}"


def normalize_birth_date(value: str) -> str:
    """Return the library birth date ``YYYY-MM-DD``.

    The smartschool library posts ``mfa`` unchanged as
    ``security_question_answer``. Its docs call that format ``YYYY-mm-dd``
    (example ``2010-05-15``). Day-month-year input is accepted and rewritten.
    """
    text = value.strip()
    parts = re.split(r"[./-]", text) if text else []
    if len(parts) == 3 and all(part.isdigit() for part in parts):
        if len(parts[0]) == 4:
            year_s, month_s, day_s = parts
        elif len(parts[2]) == 4:
            day_s, month_s, year_s = parts
        else:
            year_s = ""
        if year_s:
            try:
                parsed = date(int(year_s), int(month_s), int(day_s))
            except ValueError:
                parsed = None
            if parsed is not None and date(1980, 1, 1) <= parsed <= date.today():
                return parsed.isoformat()
    raise CredentialStoreError(
        "Geboortedatum van het kind moet jjjj-mm-dd zijn, bijvoorbeeld 2014-03-21. "
        "Geen loginpoging gedaan."
    )


_ACCOUNT_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _clean_child_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if not text or any(char in text for char in "\r\n"):
        return ""
    return text


def _child_record(item: Mapping[str, Any]) -> dict[str, str] | None:
    """One stored child. ``account_id`` and ``platform`` match ``get_children``."""
    name = ""
    for key in ("name", "first_name", "firstName"):
        name = _clean_child_text(item.get(key))
        if name:
            break
    if not name:
        return None
    record = {"name": name}
    account_id = ""
    for key in ("account_id", "accountID", "accountId"):
        raw_id = item.get(key)
        if isinstance(raw_id, int) and not isinstance(raw_id, bool) and raw_id >= 0:
            raw_id = str(raw_id)
        account_id = _clean_child_text(raw_id)
        if account_id:
            break
    if account_id and _ACCOUNT_ID_RE.fullmatch(account_id):
        record["account_id"] = account_id
    platform = ""
    for key in ("platform", "host", "main_url", "mainUrl"):
        platform = _clean_child_text(item.get(key))
        if platform:
            break
    if platform:
        record["platform"] = platform
    return record


def child_records(raw: Mapping[str, Any]) -> list[dict[str, str]]:
    """Children stored for one login, aligned with ``get_children`` identity."""
    found: list[dict[str, str]] = []
    children = raw.get("children")
    if isinstance(children, list):
        for item in children:
            if isinstance(item, str):
                record = _child_record({"name": item})
            elif isinstance(item, dict):
                record = _child_record(item)
            else:
                record = None
            if record is not None:
                found.append(record)
    legacy = raw.get("child")
    if isinstance(legacy, str):
        record = _child_record({"name": legacy})
        if record is not None:
            found.append(record)
    unique: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    seen_names: set[str] = set()
    for record in found:
        account_id = record.get("account_id", "")
        if account_id:
            if account_id in seen_ids:
                continue
            seen_ids.add(account_id)
            for index, previous in enumerate(unique):
                if previous["name"] == record["name"] and "account_id" not in previous:
                    unique[index] = record
                    seen_names.add(record["name"])
                    break
            else:
                seen_names.add(record["name"])
                unique.append(record)
            continue
        if record["name"] in seen_names:
            continue
        seen_names.add(record["name"])
        unique.append(record)
    return unique


def child_name_list(account: Mapping[str, Any]) -> list[str]:
    return [record["name"] for record in child_records(account)]


def save_credentials(
    username: str,
    password: str,
    main_url: str,
    mfa: str,
    child_name: str,
) -> Path:
    """Persist one profile. On macOS the password and birth date go to the Keychain."""
    username = _check_username(username.strip())
    password = password.strip()
    host = normalize_school(main_url)
    birth = normalize_birth_date(mfa)
    name = child_name.strip()
    if not password or not name or any(char in name for char in "\r\n"):
        raise CredentialStoreError(
            "Gebruikersnaam, wachtwoord, geboortedatum en naam van het kind "
            "zijn verplicht. Niets opgeslagen. Geen loginpoging gedaan."
        )
    _reject_newlines(password)
    profile = {
        "main_url": host,
        "username": username,
        "password": password,
        "mfa": birth,
        "children": [{"name": name}],
    }
    existing = _read_profile_documents()
    pid = profile_id(host, username)
    merged: list[dict[str, Any]] = []
    replaced = False
    for item in existing:
        if profile_id(item["main_url"], item["username"]) == pid:
            merged.append(profile)
            replaced = True
        else:
            merged.append(item)
    if not replaced:
        merged.append(profile)
    _write_profiles(merged)
    return credentials_path()


def _string_field(raw: Mapping[str, Any], key: str) -> str:
    value = raw.get(key, "")
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise CredentialStoreError(
            "Het credentialbestand is onleesbaar. Geen loginpoging gedaan."
        )
    return value.strip()


def _profile_from_raw(raw: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "main_url": _string_field(raw, "main_url"),
        "username": _string_field(raw, "username"),
        "password": _string_field(raw, "password"),
        "mfa": _string_field(raw, "mfa"),
        "children": child_records(raw),
    }


def _parse_document(text: str) -> list[dict[str, Any]]:
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        raise CredentialStoreError(
            "Het credentialbestand is onleesbaar. Geen loginpoging gedaan."
        ) from None
    if not isinstance(raw, dict):
        raise CredentialStoreError(
            "Het credentialbestand is onleesbaar. Geen loginpoging gedaan."
        )
    profiles = raw.get("profiles")
    if isinstance(profiles, list):
        parsed: list[dict[str, Any]] = []
        for item in profiles:
            if not isinstance(item, dict):
                raise CredentialStoreError(
                    "Het credentialbestand is onleesbaar. Geen loginpoging gedaan."
                )
            parsed.append(_profile_from_raw(item))
        return parsed
    if "username" in raw or "main_url" in raw:
        return [_profile_from_raw(raw)]
    raise CredentialStoreError(
        "Het credentialbestand is onleesbaar. Geen loginpoging gedaan."
    )


def _read_profile_documents() -> list[dict[str, Any]]:
    path = credentials_path()
    if not path.is_file():
        return []
    return _parse_document(path.read_text(encoding="utf-8"))


def _fill_profile_secrets(profile: dict[str, Any]) -> dict[str, Any]:
    if profile["username"] and profile["main_url"] and not profile["password"]:
        if not keychain_enabled():
            raise MissingCredentialsError(
                "Het credentialbestand mist een wachtwoord. Geen loginpoging gedaan."
            )
        try:
            host = normalize_school(profile["main_url"])
        except SmartSchoolAuthenticationError as exc:
            raise CredentialStoreError(str(exc)) from None
        account = _keychain_account(profile["username"], host)
        password = keychain_get(_KEYCHAIN_SERVICE, account)
        birth = profile["mfa"] or keychain_get(_KEYCHAIN_MFA_SERVICE, account) or ""
        if not password or not birth:
            raise MissingCredentialsError(
                "Bewaard wachtwoord ontbreekt in de Sleutelhanger. "
                "Gebruik login.py --reset. Geen loginpoging gedaan."
            )
        profile["password"] = password
        profile["mfa"] = birth
    return profile


def _write_profiles(profiles: list[dict[str, Any]]) -> None:
    public: list[dict[str, Any]] = []
    use_keychain = keychain_enabled()
    for profile in profiles:
        item: dict[str, Any] = {
            "main_url": profile["main_url"],
            "username": profile["username"],
            "children": profile.get("children") or [],
        }
        password = str(profile.get("password") or "")
        birth = str(profile.get("mfa") or "")
        if use_keychain:
            account = _keychain_account(profile["username"], profile["main_url"])
            if password:
                keychain_set(_KEYCHAIN_SERVICE, account, password)
            if birth:
                keychain_set(_KEYCHAIN_MFA_SERVICE, account, birth)
        else:
            item["password"] = password
            item["mfa"] = birth
        public.append(item)
    path = credentials_path()
    if not public:
        if path.is_file():
            path.unlink()
        return
    _write_private(
        path, json.dumps({"profiles": public}, ensure_ascii=False, indent=2) + "\n"
    )


def load_profiles() -> list[dict[str, Any]]:
    """Saved profiles with secrets filled in. Do not log or print the result."""
    return [_fill_profile_secrets(profile) for profile in _read_profile_documents()]


def read_saved_credentials() -> dict[str, Any] | None:
    """Return the only saved profile, or the one named by ``SMARTSCHOOL_PROFILE``.

    ``None`` when no file exists. The dict is for in-process use only.
    """
    profiles = load_profiles()
    if not profiles:
        return None
    selector = os.environ.get("SMARTSCHOOL_PROFILE", "").strip()
    if selector:
        matched = select_profiles(profiles, selector)
        if len(matched) == 1:
            return matched[0]
        if not matched:
            return None
        raise MissingCredentialsError(_ambiguous_profile_message(selector, matched))
    if len(profiles) == 1:
        return profiles[0]
    return None


def _env_username() -> str:
    user = os.environ.get("SMARTSCHOOL_USER", "").strip()
    username = os.environ.get("SMARTSCHOOL_USERNAME", "").strip()
    if user and username and user != username:
        raise CredentialMixError(
            "LOGIN STOPPED, geen poging gedaan. SMARTSCHOOL_USER en "
            "SMARTSCHOOL_USERNAME verschillen. Gebruik één gebruikersnaam."
        )
    return user or username


def _sync_user_alias() -> None:
    username = _env_username()
    if username:
        os.environ["SMARTSCHOOL_USERNAME"] = username


def _nonempty(name: str) -> str:
    return os.environ.get(name, "").strip()


def _legacy_credential_files() -> list[Path]:
    """Account files from before the shared store. Missing files are skipped.

    ``SMARTSCHOOL_CONFIG`` is explicit and must exist. Otherwise an existing
    plugin ``config.env`` and the current ``.env`` are seeds, not a second store.
    """
    explicit = os.environ.get("SMARTSCHOOL_CONFIG", "").strip()
    if explicit:
        chosen = Path(explicit).expanduser()
        if not chosen.is_file():
            raise FileNotFoundError(f"Credential file not found: {chosen}")
        return [chosen]
    found: list[Path] = []
    if _PLUGIN_CONFIG.is_file():
        found.append(_PLUGIN_CONFIG)
    dotenv = Path.cwd() / ".env"
    if dotenv.is_file():
        found.append(dotenv.resolve())
    unique: list[Path] = []
    for path in found:
        if path not in unique:
            unique.append(path)
    return unique


def _account_from_dotenv(path: Path) -> dict[str, Any] | None:
    parsed = parse_env_file(path.read_text(encoding="utf-8"))
    username = (
        parsed.get("SMARTSCHOOL_USERNAME", "").strip()
        or parsed.get("SMARTSCHOOL_USER", "").strip()
    )
    password = parsed.get("SMARTSCHOOL_PASSWORD", "").strip()
    host = parsed.get("SMARTSCHOOL_MAIN_URL", "").strip()
    birth = parsed.get("SMARTSCHOOL_MFA", "").strip()
    if not (username and password and host and birth):
        return None
    if {username, password, host, birth} & _LEGACY_PLACEHOLDERS:
        return None
    child = parsed.get("SMARTSCHOOL_CHILD", "").strip()
    return {
        "main_url": normalize_school(host),
        "username": _check_username(username),
        "password": password,
        "mfa": normalize_birth_date(birth),
        "children": [{"name": child}] if child else [],
    }


def _maybe_seed_legacy() -> None:
    """Copy one legacy account into the shared file when that file is absent."""
    if _read_profile_documents():
        return
    if _env_username() or _nonempty("SMARTSCHOOL_PASSWORD"):
        return
    seeded: list[dict[str, Any]] = []
    for path in _legacy_credential_files():
        account = _account_from_dotenv(path)
        if account is None:
            continue
        if seeded and (
            profile_id(seeded[0]["main_url"], seeded[0]["username"])
            != profile_id(account["main_url"], account["username"])
            or seeded[0]["password"] != account["password"]
        ):
            raise CredentialMixError(
                "LOGIN STOPPED, geen poging gedaan. Oude configbestanden "
                "geven verschillende Smartschool-gegevens. Gebruik het "
                "gedeelde credentialbestand. Niet opnieuw proberen met een mix."
            )
        if not seeded:
            seeded.append(account)
    if seeded:
        _write_profiles(seeded)


def _fill_host_and_mfa(profiles: list[dict[str, Any]]) -> None:
    """Host and birth date: environment, then one saved profile."""
    need_host = not _nonempty("SMARTSCHOOL_MAIN_URL")
    need_mfa = not _nonempty("SMARTSCHOOL_MFA")
    if not need_host and not need_mfa:
        return
    username = _env_username()
    matches = [
        profile
        for profile in profiles
        if not username or profile.get("username") == username
    ]
    saved = matches[0] if len(matches) == 1 else None
    if saved is None and len(profiles) == 1:
        saved = profiles[0]
    if saved is None:
        return
    if need_host and saved.get("main_url"):
        os.environ["SMARTSCHOOL_MAIN_URL"] = str(saved["main_url"])
    if need_mfa and saved.get("mfa"):
        os.environ["SMARTSCHOOL_MFA"] = str(saved["mfa"])


def _apply_saved(saved: Mapping[str, Any]) -> None:
    username = _check_username(str(saved.get("username", "")).strip())
    password = str(saved.get("password", "")).strip()
    mfa = _nonempty("SMARTSCHOOL_MFA") or str(saved.get("mfa", "")).strip()
    host_value = _nonempty("SMARTSCHOOL_MAIN_URL") or str(saved.get("main_url", ""))
    if not password or not mfa or not host_value.strip():
        raise MissingCredentialsError(
            "Bewaarde Smartschool-gegevens zijn onvolledig. Geen loginpoging gedaan."
        )
    host = normalize_school(host_value)
    birth = normalize_birth_date(mfa)
    os.environ["SMARTSCHOOL_USER"] = username
    os.environ["SMARTSCHOOL_USERNAME"] = username
    os.environ["SMARTSCHOOL_PASSWORD"] = password
    os.environ["SMARTSCHOOL_MAIN_URL"] = host
    os.environ["SMARTSCHOOL_MFA"] = birth
    names = child_name_list(saved)
    if names:
        os.environ["SMARTSCHOOL_CHILD"] = names[0]


def _stdin_is_tty() -> bool:
    try:
        return sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


def _ask(label: str) -> str:
    print(label, file=sys.stderr, end="", flush=True)
    return input()


def _profile_label(profile: Mapping[str, Any]) -> str:
    names = child_name_list(profile)
    pid = profile_id(str(profile.get("main_url", "")), str(profile.get("username", "")))
    if names:
        return f"{pid} ({names[0]})"
    return pid


def _ambiguous_profile_message(selector: str, profiles: list[dict[str, Any]]) -> str:
    labels = ", ".join(_profile_label(profile) for profile in profiles)
    return (
        f"Meerdere profielen voor {selector}: {labels}. "
        "Kies subdomain:gebruiker. Geen loginpoging gedaan."
    )


def select_profiles(
    profiles: list[dict[str, Any]], selector: str
) -> list[dict[str, Any]]:
    """Match a subdomain, ``subdomain:user``, or ``user@subdomain``."""
    token = selector.strip().lower()
    if not token:
        return list(profiles)
    user = ""
    host = ""
    if "@" in token:
        user, _, school = token.partition("@")
        host = normalize_school(school)
    elif ":" in token:
        school, _, user = token.partition(":")
        host = normalize_school(school)
    else:
        try:
            host = normalize_school(token)
        except SmartSchoolAuthenticationError:
            user = token
    matched: list[dict[str, Any]] = []
    for profile in profiles:
        try:
            phost = normalize_school(str(profile.get("main_url", "")))
        except SmartSchoolAuthenticationError:
            continue
        puser = str(profile.get("username", "")).lower()
        same_user = bool(user) and puser == user.lower()
        same_host = bool(host) and phost == host
        if (
            (user and host and same_user and same_host)
            or (host and not user and same_host)
            or (user and not host and same_user)
        ):
            matched.append(profile)
    return matched


def _selector_needs_user(selector: str) -> bool:
    token = selector.strip()
    return bool(token) and "@" not in token and ":" not in token


def prompt_and_save_credentials(prefill_school: str = "") -> dict[str, Any]:
    """Ask once on a TTY and store one profile. Does not contact Smartschool."""
    print(
        "Smartschool-profiel, eenmalig lokaal opgeslagen. "
        "Wachtwoord en geboortedatum worden niet getoond.",
        file=sys.stderr,
    )
    if prefill_school:
        host_input = prefill_school
    else:
        host_input = _ask("School (subdomein, bv. dering of depass): ").strip()
    host = normalize_school(host_input)
    username = _ask("Gebruikersnaam: ").strip()
    password = getpass.getpass("Wachtwoord: ")
    birth = getpass.getpass("Geboortedatum van het kind (jjjj-mm-dd): ")
    child = _ask("Naam van het kind: ").strip()
    save_credentials(username, password, host, birth, child)
    account = {
        "username": username.strip(),
        "password": password.strip(),
        "main_url": host,
        "mfa": normalize_birth_date(birth),
        "children": [{"name": child.strip()}],
    }
    _apply_saved(account)
    return account


def _require_host_and_mfa() -> None:
    missing = [
        name
        for name in ("SMARTSCHOOL_MAIN_URL", "SMARTSCHOOL_MFA")
        if not _nonempty(name)
    ]
    if missing:
        joined = " en ".join(missing)
        raise MissingCredentialsError(f"{joined} ontbreekt. Geen loginpoging gedaan.")
    os.environ["SMARTSCHOOL_MAIN_URL"] = normalize_school(
        _nonempty("SMARTSCHOOL_MAIN_URL")
    )
    os.environ["SMARTSCHOOL_MFA"] = normalize_birth_date(_nonempty("SMARTSCHOOL_MFA"))


def _account_from_env(profiles: list[dict[str, Any]]) -> dict[str, Any]:
    host = normalize_school(_nonempty("SMARTSCHOOL_MAIN_URL"))
    username = _check_username(_env_username())
    names: list[str] = []
    if _nonempty("SMARTSCHOOL_CHILD"):
        names = [_nonempty("SMARTSCHOOL_CHILD")]
    else:
        for profile in profiles:
            try:
                same_host = normalize_school(str(profile.get("main_url", ""))) == host
            except SmartSchoolAuthenticationError:
                same_host = False
            if same_host and profile.get("username") == username:
                names = child_name_list(profile)
                break
    return {
        "main_url": host,
        "username": username,
        "password": _nonempty("SMARTSCHOOL_PASSWORD"),
        "mfa": _nonempty("SMARTSCHOOL_MFA"),
        "children": [{"name": name} for name in names],
    }


def resolve_accounts(*, prompt: bool = True) -> list[dict[str, Any]]:
    """Accounts for this run. No network and no login POST."""
    _sync_user_alias()
    _maybe_seed_legacy()
    profiles = load_profiles()
    if _env_username() and _nonempty("SMARTSCHOOL_PASSWORD"):
        _fill_host_and_mfa(profiles)
        _require_host_and_mfa()
        return [_account_from_env(profiles)]

    selector = _nonempty("SMARTSCHOOL_PROFILE")
    if profiles:
        if not selector:
            return profiles
        matched = select_profiles(profiles, selector)
        if _selector_needs_user(selector) and len(matched) > 1:
            raise MissingCredentialsError(_ambiguous_profile_message(selector, matched))
        if len(matched) == 1:
            return matched
        if len(matched) > 1:
            return matched
        if prompt and _stdin_is_tty():
            return [prompt_and_save_credentials(selector)]
        raise MissingCredentialsError(
            f"Geen profiel {selector}. Geen loginpoging gedaan."
        )

    if prompt and selector and _stdin_is_tty():
        return [prompt_and_save_credentials(selector)]

    if prompt and _stdin_is_tty():
        return [prompt_and_save_credentials()]
    raise MissingCredentialsError(_NO_CREDENTIALS)


def ensure_credentials() -> None:
    """Resolve one account into the environment. No network."""
    accounts = resolve_accounts(prompt=True)
    if len(accounts) == 1:
        _apply_saved(accounts[0])
        return
    labels = ", ".join(_profile_label(account) for account in accounts)
    raise MissingCredentialsError(
        f"Meerdere profielen ({labels}). Kies met --profile of "
        "SMARTSCHOOL_PROFILE. Geen loginpoging gedaan."
    )


def activate_saved_credentials() -> None:
    """Apply one saved account to the environment. No prompt and no network.

    A missing account leaves the environment unchanged. Several saved profiles
    and no ``SMARTSCHOOL_PROFILE`` raise, so a server does not guess a child.
    """
    try:
        accounts = resolve_accounts(prompt=False)
    except MissingCredentialsError:
        return
    if len(accounts) == 1:
        _apply_saved(accounts[0])
        return
    labels = ", ".join(_profile_label(account) for account in accounts)
    raise MissingCredentialsError(
        f"Meerdere profielen ({labels}). Kies met SMARTSCHOOL_PROFILE. "
        "Geen loginpoging gedaan."
    )


def _strip_unfilled_user_config() -> None:
    """Drop unsubstituted mcpb placeholders. They are not a school name."""
    for key, value in list(os.environ.items()):
        if not key.startswith("SMARTSCHOOL_"):
            continue
        text = value.strip()
        if text.startswith("${user_config.") and text.endswith("}"):
            os.environ.pop(key, None)


def _profiles_with_id(
    profiles: list[dict[str, Any]], host: str, username: str
) -> list[dict[str, Any]]:
    try:
        want = profile_id(host, username)
    except (SmartSchoolAuthenticationError, CredentialStoreError):
        return []
    matched: list[dict[str, Any]] = []
    for profile in profiles:
        try:
            pid = profile_id(
                str(profile.get("main_url", "")), str(profile.get("username", ""))
            )
        except (SmartSchoolAuthenticationError, CredentialStoreError):
            continue
        if pid == want:
            matched.append(profile)
    return matched


def _desktop_multiple_message(profiles: list[dict[str, Any]]) -> str:
    labels = ", ".join(_profile_label(profile) for profile in profiles)
    return (
        f"Meerdere profielen ({labels}). Kies er één: open in Claude Desktop "
        "Instellingen > Extensies > Smartschool > Configure en vul de school "
        "en de gebruikersnaam van dat profiel in. Sluit daarna Claude helemaal "
        "af en open het opnieuw. Geen loginpoging gedaan."
    )


def _checked_dialog_host(school: str, username: str) -> tuple[str, str]:
    try:
        return normalize_school(school), _check_username(username)
    except (SmartSchoolAuthenticationError, CredentialStoreError) as exc:
        raise MissingCredentialsError(str(exc)) from None


def _save_dialog_or_keep_store(
    profiles: list[dict[str, Any]],
    school: str,
    username: str,
    password: str,
    birth: str,
    child_name: str,
) -> None:
    """Save a complete Configure dialog once, unless that profile is stored.

    An existing profile is applied as stored. Nothing is logged and Smartschool
    is not contacted.
    """
    host, user = _checked_dialog_host(school, username)
    try:
        normalize_birth_date(birth)
    except CredentialStoreError as exc:
        raise MissingCredentialsError(str(exc)) from None
    matches = _profiles_with_id(profiles, host, user)
    if not matches:
        save_credentials(user, password, host, birth, child_name)
        matches = _profiles_with_id(load_profiles(), host, user)
    if len(matches) != 1:
        raise MissingCredentialsError(INCOMPLETE_DESKTOP_CREDENTIALS)
    _apply_saved(matches[0])


def _activate_process_override(profiles: list[dict[str, Any]]) -> None:
    """Use a full env account that is not the Configure dialog. Do not save it."""
    try:
        activate_saved_credentials()
    except MissingCredentialsError as exc:
        if str(exc).startswith("Meerdere profielen"):
            raise MissingCredentialsError(_desktop_multiple_message(profiles)) from None
        raise
    if (
        _env_username()
        and _nonempty("SMARTSCHOOL_PASSWORD")
        and _nonempty("SMARTSCHOOL_MAIN_URL")
        and _nonempty("SMARTSCHOOL_MFA")
    ):
        return
    if len(profiles) > 1:
        raise MissingCredentialsError(_desktop_multiple_message(profiles))
    if profiles or _env_username() or _nonempty("SMARTSCHOOL_PASSWORD"):
        raise MissingCredentialsError(INCOMPLETE_DESKTOP_CREDENTIALS)
    raise MissingCredentialsError(NO_DESKTOP_CREDENTIALS)


def _select_stored_profile(
    profiles: list[dict[str, Any]], school: str, username: str
) -> None:
    host, user = _checked_dialog_host(school, username)
    matches = _profiles_with_id(profiles, host, user)
    if len(matches) == 1:
        _apply_saved(matches[0])
        return
    if len(profiles) > 1 or len(matches) > 1:
        raise MissingCredentialsError(_desktop_multiple_message(matches or profiles))
    raise MissingCredentialsError(INCOMPLETE_DESKTOP_CREDENTIALS)


def prepare_server_credentials() -> None:
    """Apply the central store, or save a complete Configure dialog once.

    A matching stored profile wins and is not copied again. A missing account
    raises a parent-facing message. No login and no logging of secrets.
    """
    _strip_unfilled_user_config()
    _maybe_seed_legacy()
    _sync_user_alias()
    profiles = load_profiles()
    school = _nonempty("SMARTSCHOOL_MAIN_URL")
    username = _env_username()
    password = _nonempty("SMARTSCHOOL_PASSWORD")
    birth = _nonempty("SMARTSCHOOL_MFA")
    child_name = _nonempty("SMARTSCHOOL_CHILD_NAME")
    if school and username and password and birth and child_name:
        _save_dialog_or_keep_store(
            profiles, school, username, password, birth, child_name
        )
        return
    if password and username and not child_name:
        _activate_process_override(profiles)
        return
    if school and username:
        _select_stored_profile(profiles, school, username)
        return
    if child_name or password or birth or school or username:
        if len(profiles) == 1 and not school and not username:
            _apply_saved(profiles[0])
            return
        raise MissingCredentialsError(INCOMPLETE_DESKTOP_CREDENTIALS)
    if len(profiles) == 1:
        _apply_saved(profiles[0])
        return
    if len(profiles) > 1:
        raise MissingCredentialsError(_desktop_multiple_message(profiles))
    raise MissingCredentialsError(NO_DESKTOP_CREDENTIALS)


def profile_cache_dir(main_url: str, username: str) -> Path:
    """Cookie cache for one login: ``~/.cache/smartschool/<subdomain>/<user>``."""
    return (
        Path.home()
        / ".cache"
        / "smartschool"
        / subdomain_of(main_url)
        / _check_username(username)
    )


def _wipe_profile_cache(profile: Mapping[str, Any]) -> None:
    with contextlib.suppress(CredentialStoreError, SmartSchoolAuthenticationError):
        cache = profile_cache_dir(
            str(profile.get("main_url", "")), str(profile.get("username", ""))
        )
        if cache.exists():
            shutil.rmtree(cache)


def _public_profile(profile: Mapping[str, Any]) -> dict[str, Any]:
    names = child_name_list(profile)
    try:
        pid: str | None = profile_id(
            str(profile.get("main_url", "")), str(profile.get("username", ""))
        )
    except (SmartSchoolAuthenticationError, CredentialStoreError):
        pid = None
    return {
        "profile": pid,
        "child": names[0] if names else None,
        "children": names,
    }


def clear_saved_credentials() -> dict[str, Any]:
    """Delete one profile, its Keychain items, and its session cache.

    With several profiles and no ``SMARTSCHOOL_PROFILE``, nothing is deleted.
    On a TTY, ask for a replacement profile. Does not log in.
    """
    try:
        profiles = _read_profile_documents()
    except CredentialStoreError:
        profiles = []
    selector = _nonempty("SMARTSCHOOL_PROFILE")
    if len(profiles) > 1 and not selector:
        return {
            "ok": False,
            "error": (
                "Meerdere profielen. Kies met --profile of SMARTSCHOOL_PROFILE. "
                "Niets gewist. Geen loginpoging gedaan."
            ),
            "profiles": [_public_profile(profile) for profile in profiles],
        }
    if selector:
        matched = select_profiles(profiles, selector)
        if _selector_needs_user(selector) and len(matched) > 1:
            return {
                "ok": False,
                "error": _ambiguous_profile_message(selector, matched),
                "profiles": [_public_profile(profile) for profile in matched],
            }
        if not matched:
            return {
                "ok": False,
                "error": (
                    f"Geen profiel {selector}. Niets gewist. Geen loginpoging gedaan."
                ),
            }
        targets = matched
    else:
        targets = profiles

    remaining = [
        profile
        for profile in profiles
        if all(profile is not target for target in targets)
    ]
    _write_profiles(remaining)
    if keychain_enabled():
        for profile in targets:
            with contextlib.suppress(SmartSchoolAuthenticationError):
                account = _keychain_account(
                    str(profile.get("username", "")),
                    normalize_school(str(profile.get("main_url", ""))),
                )
                keychain_delete(_KEYCHAIN_SERVICE, account)
                keychain_delete(_KEYCHAIN_MFA_SERVICE, account)
    for profile in targets:
        _wipe_profile_cache(profile)

    saved_new = False
    if _stdin_is_tty():
        prompt_and_save_credentials(selector if _selector_needs_user(selector) else "")
        saved_new = True
    payload: dict[str, Any] = {"ok": True, "cleared": True, "saved": saved_new}
    if len(targets) == 1:
        payload.update(_public_profile(targets[0]))
    return payload


def redact(text: str) -> str:
    """Remove known secrets from a string before it is printed."""
    redacted = text
    for key in _SECRET_ENV_KEYS:
        value = os.environ.get(key, "")
        if len(value) >= 4 and value in redacted:
            redacted = redacted.replace(value, "***")
    return redacted
