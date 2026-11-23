#!/usr/bin/env python3
"""Build the Claude Desktop ``.mcpb`` and vendor smartschool@517de70.

The repo pins that fork as a git dependency. Claude Desktop installs the
bundle with uv and may not have git, so this script downloads the archive,
puts it at ``vendor/smartschool``, and points the bundle ``pyproject.toml``
at that path. Other dependencies stay in the lockfile for uv to install
from PyPI on the parent's machine (platform wheels such as pydantic).

Pass ``--version`` (or a git tag such as ``v0.3.0``) to stamp both the
bundle ``manifest.json`` and the staged ``pyproject.toml``. Claude Desktop
only offers Update when that manifest version changes. Project versions use
PEP 440 (``0.3.0rc1``); the manifest keeps the human form (``0.3.0-rc.1``).

No Smartschool login is performed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SMARTSCHOOL_REV = "517de70b3bf227e726c1b7e2eb4d670afccc1e1a"
ARCHIVE_URL = (
    f"https://github.com/markminnoye/smartschool/archive/{SMARTSCHOOL_REV}.tar.gz"
)
_DROP_VENDOR_DIRS = ("tests", ".github", "docs", "dev", ".claude", "scripts")
_DROP_VENDOR_FILES = ("uv.lock", "sonar-project.properties", "codecov.yml", "restub")
_DEFAULT_VERSION = "1.0.0"


def assert_repo_pin(root: Path = ROOT) -> None:
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    if 'rev = "517de70"' not in text:
        raise SystemExit("pyproject.toml no longer pins smartschool rev 517de70")


def normalize_manifest_version(raw: str) -> str:
    """Strip a leading ``v`` from a tag or version string."""
    text = raw.strip()
    if not text:
        raise SystemExit("version must not be empty")
    if text.lower().startswith("v") and len(text) > 1 and text[1].isdigit():
        text = text[1:]
    if not re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z.+_-]*", text):
        raise SystemExit(f"invalid version: {raw!r}")
    return text


def pep440_version(manifest_version: str) -> str:
    """Convert a manifest / SemVer-ish string to a PEP 440 project version.

    ``uv lock`` rejects ``0.3.0-rc.1``; it accepts ``0.3.0rc1``.
    """
    text = normalize_manifest_version(manifest_version)
    match = re.fullmatch(
        r"([0-9]+(?:\.[0-9]+)*)(?:-(a|b|rc)\.?([0-9]+))?$",
        text,
        flags=re.IGNORECASE,
    )
    if match is None:
        return text
    base, pre, pre_n = match.group(1), match.group(2), match.group(3)
    if pre and pre_n:
        return f"{base}{pre.lower()}{pre_n}"
    return base


def default_output_path(manifest_version: str | None = None) -> Path:
    if manifest_version:
        safe = normalize_manifest_version(manifest_version)
        return ROOT / "dist" / f"smartschool-mcp-{safe}.mcpb"
    return ROOT / "dist" / "smartschool-mcp.mcpb"


def bundle_pyproject(version: str = _DEFAULT_VERSION) -> str:
    """Runtime project for the bundle. The fork is a path, not a git clone."""
    project_version = pep440_version(version)
    return f"""[project]
name = "smartschool-mcp"
version = "{project_version}"
description = "Smartschool MCP server for Claude Desktop"
requires-python = ">=3.10"
dependencies = [
    "cachetools>=7.1.4",
    "mcp[cli]>=1.28.1,<2",
    "pyotp>=2.9.0,<3",
    "smartschool",
]

[project.scripts]
smartschool-mcp = "smartschool_mcp.__main__:main"

[tool.uv.sources]
# Vendored markminnoye/smartschool@{SMARTSCHOOL_REV}
smartschool = {{ path = "vendor/smartschool" }}

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["smartschool_mcp"]
"""


def stamp_manifest_version(manifest_path: Path, version: str) -> str:
    """Write ``version`` into a staged ``manifest.json``. Returns the stamped value."""
    stamped = normalize_manifest_version(version)
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data["version"] = stamped
    manifest_path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return stamped


def _safe_extract(archive: tarfile.TarFile, dest: Path) -> None:
    dest_resolved = dest.resolve()
    for member in archive.getmembers():
        target = (dest / member.name).resolve()
        if os.path.commonpath([dest_resolved, target]) != str(dest_resolved):
            raise RuntimeError(f"Refusing archive path {member.name}")
    extract_kwargs: dict[str, str] = {}
    if "filter" in tarfile.TarFile.extractall.__code__.co_varnames:
        extract_kwargs["filter"] = "data"
    archive.extractall(dest, **extract_kwargs)


def download_smartschool(dest: Path) -> Path:
    """Download the pinned fork archive and return its project root."""
    dest.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as handle:
        archive_path = Path(handle.name)
    try:
        urllib.request.urlretrieve(ARCHIVE_URL, archive_path)
        with tarfile.open(archive_path, "r:gz") as archive:
            _safe_extract(archive, dest)
    finally:
        archive_path.unlink(missing_ok=True)
    extracted = dest / f"smartschool-{SMARTSCHOOL_REV}"
    if not (extracted / "pyproject.toml").is_file():
        raise SystemExit(f"Smartschool archive did not contain {extracted}")
    return extracted


def _ignore_package(_directory: str, names: list[str]) -> set[str]:
    return {name for name in names if name == "__pycache__" or name.endswith(".pyc")}


def stage_bundle(
    dest: Path,
    vendor_project: Path,
    *,
    version: str | None = None,
) -> str:
    """Fill ``dest`` with the manifest, package, and vendored fork.

    Returns the manifest version written into the staged tree.
    """
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    shutil.copy2(ROOT / "manifest.json", dest / "manifest.json")
    icon = ROOT / "icon.png"
    if not icon.is_file():
        raise SystemExit("icon.png is missing; the bundle manifest points at it")
    shutil.copy2(icon, dest / "icon.png")
    shutil.copytree(
        ROOT / "smartschool_mcp",
        dest / "smartschool_mcp",
        ignore=_ignore_package,
    )
    vendor_dest = dest / "vendor" / "smartschool"
    shutil.copytree(vendor_project, vendor_dest, ignore=_ignore_package)
    for name in _DROP_VENDOR_DIRS:
        extra = vendor_dest / name
        if extra.exists():
            shutil.rmtree(extra)
    for name in _DROP_VENDOR_FILES:
        extra = vendor_dest / name
        if extra.is_file():
            extra.unlink()
    if version is None:
        data = json.loads((dest / "manifest.json").read_text(encoding="utf-8"))
        stamped = str(data.get("version") or _DEFAULT_VERSION)
    else:
        stamped = stamp_manifest_version(dest / "manifest.json", version)
    (dest / "pyproject.toml").write_text(
        bundle_pyproject(stamped),
        encoding="utf-8",
    )
    (dest / ".mcpbignore").write_text(
        "\n".join(
            [
                "__pycache__",
                "*.pyc",
                ".git",
                "tests",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return stamped


def _run(command: list[str], cwd: Path) -> None:
    subprocess.run(command, cwd=cwd, check=True)


def pack(staging: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    _run(["uv", "lock"], staging)
    _run(["mcpb", "validate", str(staging / "manifest.json")], staging)
    if output.exists():
        output.unlink()
    _run(["mcpb", "pack", str(staging), str(output)], staging)


def build(output: Path, *, version: str | None = None) -> Path:
    assert_repo_pin()
    with tempfile.TemporaryDirectory(prefix="smartschool-mcpb-") as tmp:
        tmp_path = Path(tmp)
        vendor = download_smartschool(tmp_path / "src")
        staging = tmp_path / "bundle"
        stage_bundle(staging, vendor, version=version)
        # Keep the staged tree only long enough to pack; uv.lock is inside it.
        pack(staging, output)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--version",
        default=None,
        help=(
            "Version or git tag to stamp into the bundle "
            "(e.g. 0.3.0 or v0.3.0-rc.2). Defaults to manifest.json."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Path of the .mcpb file to write",
    )
    args = parser.parse_args()
    version = (
        normalize_manifest_version(args.version) if args.version is not None else None
    )
    if args.output is None:
        output = default_output_path(version)
    else:
        output = args.output if args.output.is_absolute() else ROOT / args.output
    built = build(output, version=version)
    print(f"Wrote {built}")


if __name__ == "__main__":
    main()
