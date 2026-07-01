"""A byor-free .pre-commit-config.yaml that gates on the committed rules and checks.

Each hook runs `ast-grep scan --error` or a committed check command directly, so
the team enforces the gate through the standard `pre-commit` tool with no byor
dependency. pre-commit passes the staged matching files to each hook, mirroring
byor's own per-file scoping. The file is shared: byor owns it only when it
carries the marker header, regenerating it wholesale then; a user-owned config
is never touched, and byor hands back the block to paste in instead.
"""

from __future__ import annotations

from pathlib import Path

from byor.config import CheckDef
from byor.io.fsio import MANAGED_NOTICE, write_marked_text

CONFIG_RELPATH = ".pre-commit-config.yaml"

GATE_MARKER = f"# {MANAGED_NOTICE}"


def write_precommit_config(repo_root: Path, checks: list[CheckDef]) -> list[str]:
    """Converge .pre-commit-config.yaml, or return the block to add if user-owned."""
    content = f"{GATE_MARKER}\nrepos:\n{_local_repo_block(checks)}"
    result = write_marked_text(repo_root / CONFIG_RELPATH, content, GATE_MARKER)
    if result == "unmarked":
        return [
            f"{CONFIG_RELPATH} already exists; add this to its `repos:` list:",
            _local_repo_block(checks),
        ]
    if result == "written":
        return [f"Wrote {CONFIG_RELPATH}"]
    return []


def _local_repo_block(checks: list[CheckDef]) -> str:
    hooks = [_hook("byor-ast-grep", "ast-grep scan", "ast-grep scan --error", [])]
    hooks.extend(
        _hook(f"byor-{check.name}", check.name, check.run, check.extensions)
        for check in checks
    )
    return "  - repo: local\n    hooks:\n" + "\n".join(hooks) + "\n"


def _hook(hook_id: str, name: str, entry: str, extensions: list[str]) -> str:
    lines = [
        f"      - id: {hook_id}",
        f"        name: {name}",
        f"        entry: {entry}",
        "        language: system",
    ]
    if extensions:
        pattern = "|".join(extension.lstrip(".") for extension in extensions)
        lines.append(rf"        files: \.({pattern})$")
    return "\n".join(lines)
