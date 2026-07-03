"""Render GitHub Actions enforcement for BYOR.

The CI scaffold writes a managed workflow that installs tooling and runs the same BYOR gate used
locally. Keeping workflow text generated from checks ensures repository enforcement tracks
configuration changes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from byor.io.fsio import write_marked_text
from byor.scaffold.precommit import GATE_MARKER, ast_grep_entry

if TYPE_CHECKING:
    from pathlib import Path
    from typing import Literal

    from byor.config import CheckDef

__all__ = (
    "workflow_text",
    "write_ci_workflow",
)

WORKFLOW_RELPATH = ".github/workflows/byor-gate.yml"


def write_ci_workflow(
    repo_root: Path, checks: list[CheckDef], *, default_branch: str, fail_on: Literal["all", "error"]
) -> list[str]:
    text = workflow_text(checks, default_branch=default_branch, fail_on=fail_on)
    result = write_marked_text(repo_root / WORKFLOW_RELPATH, text, marker=GATE_MARKER)
    if result == "unmarked":
        # An unmarked workflow is user-owned; without this message the gate
        # would be recorded as installed while CI silently enforces nothing.
        return [
            f"{WORKFLOW_RELPATH} already exists; add these steps to one of its jobs:",
            "\n".join(_gate_steps(checks, fail_on)),
        ]
    if result == "written":
        return [f"Wrote {WORKFLOW_RELPATH}"]
    return []


def _gate_steps(checks: list[CheckDef], fail_on: Literal["all", "error"]) -> list[str]:
    steps = [
        "      - uses: actions/checkout@v4",
        "      - uses: astral-sh/setup-uv@v6",
        "        with:",
        "          enable-cache: true",
        f"      - run: {ast_grep_entry(fail_on)}",
    ]
    steps.extend(f"      - run: {check.run}" for check in checks)
    return steps


def workflow_text(checks: list[CheckDef], *, default_branch: str, fail_on: Literal["all", "error"]) -> str:
    steps = _gate_steps(checks, fail_on)
    return (
        f"{GATE_MARKER}\n"
        "name: byor gate\n"
        # Push runs are limited to the default branch so a PR branch is not
        # gated twice (once by push, once by pull_request).
        "on:\n"
        "  pull_request:\n"
        "  push:\n"
        f"    branches: [{default_branch}]\n"
        "jobs:\n"
        "  byor:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n" + "\n".join(steps) + "\n"
    )
