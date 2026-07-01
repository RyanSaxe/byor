#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Reject thin docstrings on classes, methods, and functions.

Function-level docstrings should be rare in greenfield code because strong
names, precise type signatures, and straightforward bodies usually explain the
contract better than a redundant sentence. Keep one only for public APIs that
need real argument and behavior documentation, or for complex signatures whose
meaning requires one or more explanatory paragraphs.
"""

from __future__ import annotations

import ast
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

PYTHON_SUFFIXES = frozenset({".py", ".pyi"})
EXCLUDED_WALK_DIRS = frozenset({".git", ".venv", "venv", "node_modules", "__pycache__", ".tox", "dist", "build"})
MAX_THIN_LINES = 3
THIN_DOCSTRING_ADVICE = (
    "delete it if the signature and body are enough, or expand it into "
    "full public/complexity docs with arguments, behavior, edge cases, "
    "and examples where useful; when expanding, build on what it already "
    "says — never swap real information for generic filler"
)

DOC_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


@dataclass(frozen=True)
class _Finding:
    path: Path
    line: int
    message: str


def main(argv: list[str]) -> int:
    findings = [finding for path in _python_files(argv) for finding in _check(path)]
    for finding in findings:
        sys.stdout.write(f"{finding.path}:{finding.line}: {finding.message}\n")
    return 1 if findings else 0


def _python_files(argv: list[str]) -> list[Path]:
    candidates = [Path(raw) for raw in argv] if argv else _repo_python_files()
    return [path for path in candidates if path.suffix in PYTHON_SUFFIXES and path.is_file()]


def _repo_python_files() -> list[Path]:
    git = shutil.which("git")
    if git is None:
        return _walk_python_files(Path.cwd())
    root = _git_root(Path.cwd(), git=git)
    if root is None:
        return _walk_python_files(Path.cwd())
    try:
        completed = subprocess.run(
            (
                git,
                "-C",
                str(root),
                "ls-files",
                "-co",
                "--exclude-standard",
                "--",
                "*.py",
                "*.pyi",
            ),
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return _walk_python_files(root)
    if completed.returncode != 0:
        return _walk_python_files(root)
    return [root / line for line in completed.stdout.splitlines() if line]


def _git_root(start: Path, *, git: str) -> Path | None:
    anchor = start if start.is_dir() else start.parent
    try:
        completed = subprocess.run(
            (git, "-C", str(anchor), "rev-parse", "--show-toplevel"),
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return None
    if completed.returncode != 0:
        return None
    root = completed.stdout.strip()
    return Path(root) if root else None


def _walk_python_files(root: Path) -> list[Path]:
    return [
        path
        for path in root.rglob("*")
        if path.suffix in PYTHON_SUFFIXES
        and path.is_file()
        and not EXCLUDED_WALK_DIRS.intersection(path.relative_to(root).parts)
    ]


def _check(path: Path) -> list[_Finding]:
    try:
        source = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return [_Finding(path, 1, "file is not valid UTF-8; fix the encoding")]
    try:
        module = ast.parse(source, filename=str(path))
    except SyntaxError:
        return []
    findings: list[_Finding] = []
    for node in ast.walk(module):
        if not isinstance(node, DOC_NODES):
            continue
        docstring = ast.get_docstring(node)
        if docstring is None:
            continue
        if len(_meaningful_lines(docstring)) <= MAX_THIN_LINES:
            message = f"thin docstring on {_label(node)}; {THIN_DOCSTRING_ADVICE}"
            findings.append(_Finding(path, _docstring_line(node), message))
    return findings


def _meaningful_lines(docstring: str) -> list[str]:
    return [line.strip() for line in docstring.strip("\n").splitlines() if line.strip()]


def _docstring_line(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> int:
    if not node.body:
        return getattr(node, "lineno", 1)
    return getattr(node.body[0], "lineno", getattr(node, "lineno", 1))


def _label(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> str:
    kind = "class" if isinstance(node, ast.ClassDef) else "function/method"
    return f"{kind} `{node.name}`"


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
