"""Render GitHub Actions enforcement for BYOR.

The CI scaffold writes a managed workflow that installs tooling and runs the same BYOR gate used
locally. Keeping workflow text generated from checks ensures repository enforcement tracks
configuration changes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from byor.io.fsio import MANAGED_NOTICE, write_marked_text

if TYPE_CHECKING:
    from pathlib import Path

    from byor.config import CheckDef

__all__ = ("write_ci_workflow",)

WORKFLOW_RELPATH = ".github/workflows/byor-gate.yml"

GATE_MARKER = f"# {MANAGED_NOTICE}"
AST_GREP_ENTRY = "uvx --from ast-grep-cli ast-grep scan --error"


def write_ci_workflow(repo_root: Path, checks: list[CheckDef]) -> list[str]:
    result = write_marked_text(repo_root / WORKFLOW_RELPATH, _workflow_yaml(checks), marker=GATE_MARKER)
    if result == "written":
        return [f"Wrote {WORKFLOW_RELPATH}"]
    return []


def _workflow_yaml(checks: list[CheckDef]) -> str:
    steps = [
        "      - uses: actions/checkout@v4",
        "      - uses: astral-sh/setup-uv@v6",
        "        with:",
        "          enable-cache: true",
        f"      - run: {AST_GREP_ENTRY}",
    ]
    steps.extend(f"      - run: {check.run}" for check in checks)
    return (
        f"{GATE_MARKER}\n"
        "name: byor gate\n"
        "on: [push, pull_request]\n"
        "jobs:\n"
        "  byor:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n" + "\n".join(steps) + "\n"
    )
