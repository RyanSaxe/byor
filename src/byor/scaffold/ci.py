"""A byor-free CI workflow that gates on the committed rules and checks.

The generated GitHub Actions workflow runs `ast-grep scan --error` and each
committed repo check directly, so a fresh clone enforces the gate with no byor
installed. byor owns the workflow file (marker header), so it is regenerated
wholesale whenever the committed rules or checks change.
"""

from __future__ import annotations

from pathlib import Path

from byor.config import CheckDef
from byor.io.fsio import MANAGED_NOTICE, write_marked_text

WORKFLOW_RELPATH = ".github/workflows/byor-gate.yml"

GATE_MARKER = f"# {MANAGED_NOTICE}"


def write_ci_workflow(repo_root: Path, checks: list[CheckDef]) -> list[str]:
    """Converge the CI workflow to gate on project rules and `checks`."""
    result = write_marked_text(
        repo_root / WORKFLOW_RELPATH, _workflow_yaml(checks), GATE_MARKER
    )
    if result == "written":
        return [f"Wrote {WORKFLOW_RELPATH}"]
    return []


def _workflow_yaml(checks: list[CheckDef]) -> str:
    steps = [
        "      - uses: actions/checkout@v4",
        "      - run: npm install -g @ast-grep/cli",
        "      - run: ast-grep scan --error",
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
