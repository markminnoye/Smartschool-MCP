#!/usr/bin/env python3
"""Build the Claude Desktop extension (``.mcpb``).

The bundle holds the ``smartschool_mcp`` package with this repo's own
``pyproject.toml`` and ``uv.lock``, so it installs exactly the locked
dependencies: Claude Desktop runs it with ``uv``. ``--version`` (a version or
a git tag such as ``v0.4.0``) is stamped into the bundle's ``manifest.json``;
Claude Desktop only offers an update when that version changes.

Requires ``uv`` and the ``mcpb`` CLI (``npm install -g @anthropic-ai/mcpb``).
No Smartschool login is performed.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Copied into the bundle next to the package. pyproject.toml names README.md
# as its readme, so uv needs it to build the project on the user's machine.
BUNDLE_FILES = (
    "manifest.json",
    "icon.png",
    "pyproject.toml",
    "uv.lock",
    "README.md",
    "LICENSE",
)

_VERSION_RE = re.compile(
    r"[0-9]+(?:\.[0-9]+)*(?:[-+.]?[0-9A-Za-z]+(?:\.[0-9A-Za-z]+)*)?"
)


def normalize_version(raw: str) -> str:
    """``v0.4.0`` -> ``0.4.0``; anything that is not a plain version exits."""
    text = raw.strip()
    if text[:1] in {"v", "V"}:
        text = text[1:]
    if not _VERSION_RE.fullmatch(text):
        raise SystemExit(f"invalid version: {raw!r}")
    return text


def project_version(root: Path = ROOT) -> str:
    """``[project] version`` from pyproject.toml (no tomllib on Python 3.10)."""
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text)
    if match is None:
        raise SystemExit("pyproject.toml has no project version")
    return match.group(1)


def _ignore(_directory: str, names: list[str]) -> set[str]:
    return {name for name in names if name == "__pycache__" or name.endswith(".pyc")}


def stage_bundle(dest: Path, *, version: str | None = None) -> str:
    """Fill *dest* with the bundle tree; return the manifest version in it."""
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    for name in BUNDLE_FILES:
        shutil.copy2(ROOT / name, dest / name)
    shutil.copytree(ROOT / "assets", dest / "assets", ignore=_ignore)
    shutil.copytree(ROOT / "smartschool_mcp", dest / "smartschool_mcp", ignore=_ignore)

    manifest_path = dest / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if version is not None:
        manifest["version"] = normalize_version(version)
        # The server reports __version__ to the client: keep it equal.
        init = dest / "smartschool_mcp" / "__init__.py"
        init.write_text(
            re.sub(
                r'(?m)^__version__ = "[^"]*"$',
                f'__version__ = "{manifest["version"]}"',
                init.read_text(encoding="utf-8"),
            ),
            encoding="utf-8",
        )
        manifest_path.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    return str(manifest["version"])


def build(output: Path, *, version: str | None = None) -> Path:
    with tempfile.TemporaryDirectory(prefix="smartschool-mcpb-") as tmp:
        staging = Path(tmp) / "bundle"
        stage_bundle(staging, version=version)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.unlink(missing_ok=True)
        subprocess.run(["mcpb", "validate", "manifest.json"], cwd=staging, check=True)
        subprocess.run(["mcpb", "pack", str(staging), str(output)], check=True)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--version",
        help="Version or git tag to stamp into the bundle (default: manifest.json)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Where to write the .mcpb (default: dist/smartschool-mcp-<version>.mcpb)",
    )
    args = parser.parse_args()
    version = normalize_version(args.version) if args.version else None
    if args.output is None:
        name = version or json.loads((ROOT / "manifest.json").read_text())["version"]
        args.output = ROOT / "dist" / f"smartschool-mcp-{name}.mcpb"
    print(f"Wrote {build(args.output.resolve(), version=version)}")


if __name__ == "__main__":
    main()
