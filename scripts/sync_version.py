"""Synchronize BYOR's package version constant.

Release automation bumps the canonical version in `pyproject.toml` with
`uv version`. This helper mirrors that value into `src/byor/__init__.py` so
runtime version output stays deterministic without teaching release YAML how to
edit Python syntax directly.
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path
from typing import NoReturn

REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = REPO_ROOT / "pyproject.toml"
INIT_FILE = REPO_ROOT / "src" / "byor" / "__init__.py"
VERSION_PATTERN = re.compile(r'(?m)^__version__ = "([^"]+)"$')


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync src/byor/__init__.py with pyproject.toml version.")
    parser.add_argument("--check", action="store_true", help="Fail instead of writing when versions drift.")
    args = parser.parse_args()
    version = pyproject_version(PYPROJECT)
    return check_version(INIT_FILE, version) if args.check else sync_version(INIT_FILE, version)


def pyproject_version(path: Path) -> str:
    project = tomllib.loads(path.read_text(encoding="utf-8")).get("project")
    if not isinstance(project, dict):
        fail(f"{path}: missing [project] table")
    version = project.get("version")
    if not isinstance(version, str) or not version:
        fail(f"{path}: missing [project].version")
    return version


def sync_version(path: Path, version: str) -> int:
    text = path.read_text(encoding="utf-8")
    updated = replace_version(text, version)
    if updated != text:
        path.write_text(updated, encoding="utf-8")
        sys.stdout.write(f"Synced {path.relative_to(REPO_ROOT)} to {version}\n")
    return 0


def check_version(path: Path, version: str) -> int:
    current = current_version(path)
    if current == version:
        return 0
    sys.stderr.write(f"{path.relative_to(REPO_ROOT)} has {current}; pyproject.toml has {version}\n")
    return 1


def current_version(path: Path) -> str:
    match = VERSION_PATTERN.search(path.read_text(encoding="utf-8"))
    if match is None:
        fail(f"{path}: missing __version__ assignment")
    return match.group(1)


def replace_version(text: str, version: str) -> str:
    if VERSION_PATTERN.search(text) is None:
        fail(f"{INIT_FILE}: missing __version__ assignment")
    return VERSION_PATTERN.sub(f'__version__ = "{version}"', text)


def fail(message: str) -> NoReturn:
    sys.stderr.write(f"{message}\n")
    raise SystemExit(1)


if __name__ == "__main__":
    raise SystemExit(main())
