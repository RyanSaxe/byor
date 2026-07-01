"""Render pre-commit enforcement for BYOR.

The pre-commit scaffold turns repository checks and ast-grep scanning into managed local hooks.
Generated entries use uvx tooling for reproducible contributors while preserving BYOR-managed
ownership markers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from byor.io.fsio import MANAGED_NOTICE, write_marked_text

if TYPE_CHECKING:
    from pathlib import Path

    from byor.config import CheckDef

__all__ = (
    "precommit_config_text",
    "write_precommit_config",
)

CONFIG_RELPATH = ".pre-commit-config.yaml"

GATE_MARKER = f"# {MANAGED_NOTICE}"
AST_GREP_ENTRY = "uvx --from ast-grep-cli ast-grep scan --error"


def precommit_config_text(checks: list[CheckDef]) -> str:
    return f"{GATE_MARKER}\nrepos:\n{_local_repo_block(checks)}"


def write_precommit_config(repo_root: Path, checks: list[CheckDef]) -> list[str]:
    result = write_marked_text(repo_root / CONFIG_RELPATH, precommit_config_text(checks), marker=GATE_MARKER)
    if result == "unmarked":
        return [
            f"{CONFIG_RELPATH} already exists; add this to its `repos:` list:",
            _local_repo_block(checks),
        ]
    if result == "written":
        return [f"Wrote {CONFIG_RELPATH}"]
    return []


def _local_repo_block(checks: list[CheckDef]) -> str:
    hooks = [_hook("byor-ast-grep", "ast-grep scan", entry=AST_GREP_ENTRY, extensions=[])]
    hooks.extend(
        _hook(f"byor-{check.name}", check.name, entry=check.run, extensions=check.extensions) for check in checks
    )
    return "  - repo: local\n    hooks:\n" + "\n".join(hooks) + "\n"


def _hook(hook_id: str, name: str, *, entry: str, extensions: list[str]) -> str:
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
