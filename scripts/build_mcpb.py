#!/usr/bin/env python3
"""Build the Claude Desktop ``.mcpb`` and vendor smartschool@517de70.

The repo pins that fork as a git dependency. Claude Desktop installs the
bundle with uv and may not have git, so this script downloads the archive,
puts it at ``vendor/smartschool``, and points the bundle ``pyproject.toml``
at that path. Other dependencies stay in the lockfile for uv to install
from PyPI on the parent's machine (platform wheels such as pydantic).

No Smartschool login is performed.
"""

from __future__ import annotations

import argparse
import os
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


def assert_repo_pin(root: Path = ROOT) -> None:
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    if 'rev = "517de70"' not in text:
        raise SystemExit("pyproject.toml no longer pins smartschool rev 517de70")


def bundle_pyproject() -> str:
    """Runtime project for the bundle. The fork is a path, not a git clone.

    The manifest version Claude Desktop shows is ``0.3.0-test.1``. ``uv lock``
    only accepts a PEP 440 version, so this project version is the local form.
    """
    return f"""[project]
name = "smartschool-mcp"
version = "0.3.0+test.1"
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


def stage_bundle(dest: Path, vendor_project: Path) -> None:
    """Fill ``dest`` with the manifest, package, and vendored fork."""
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    shutil.copy2(ROOT / "manifest.json", dest / "manifest.json")
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
    (dest / "pyproject.toml").write_text(bundle_pyproject(), encoding="utf-8")
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


def _run(command: list[str], cwd: Path) -> None:
    subprocess.run(command, cwd=cwd, check=True)


def pack(staging: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    _run(["uv", "lock"], staging)
    _run(["mcpb", "validate", str(staging / "manifest.json")], staging)
    if output.exists():
        output.unlink()
    _run(["mcpb", "pack", str(staging), str(output)], staging)


def build(output: Path) -> Path:
    assert_repo_pin()
    with tempfile.TemporaryDirectory(prefix="smartschool-mcpb-") as tmp:
        tmp_path = Path(tmp)
        vendor = download_smartschool(tmp_path / "src")
        staging = tmp_path / "bundle"
        stage_bundle(staging, vendor)
        # Keep the staged tree only long enough to pack; uv.lock is inside it.
        pack(staging, output)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "dist" / "smartschool-mcp.mcpb",
        help="Path of the .mcpb file to write",
    )
    args = parser.parse_args()
    output = args.output if args.output.is_absolute() else ROOT / args.output
    built = build(output)
    print(f"Wrote {built}")


if __name__ == "__main__":
    main()
