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
    from typing import Literal

    from byor.config import CheckDef

__all__ = (
    "ast_grep_entry",
    "precommit_config_text",
    "write_precommit_config",
)

CONFIG_RELPATH = ".pre-commit-config.yaml"

# The single marker for both gate files (this config and the CI workflow),
# so their staleness checks stay in lockstep by construction.
GATE_MARKER = f"# {MANAGED_NOTICE}"
_AST_GREP_SCAN = "uvx --from ast-grep-cli ast-grep scan"


def ast_grep_entry(fail_on: Literal["all", "error"]) -> str:
    """Render the gate's ast-grep command, shared by the pre-commit and CI files.

    `fail_on: all` appends `--error`, escalating every rule to blocking;
    `fail_on: error` keeps ast-grep's native exit, so only error-severity
    rules block while warnings and infos still print.
    """
    return f"{_AST_GREP_SCAN} --error" if fail_on == "all" else _AST_GREP_SCAN


def precommit_config_text(checks: list[CheckDef], *, fail_on: Literal["all", "error"]) -> str:
    return f"{GATE_MARKER}\nrepos:\n{_local_repo_block(checks, fail_on)}"


def write_precommit_config(repo_root: Path, checks: list[CheckDef], *, fail_on: Literal["all", "error"]) -> list[str]:
    text = precommit_config_text(checks, fail_on=fail_on)
    result = write_marked_text(repo_root / CONFIG_RELPATH, text, marker=GATE_MARKER)
    if result == "unmarked":
        return [
            f"{CONFIG_RELPATH} already exists; add this to its `repos:` list:",
            _local_repo_block(checks, fail_on),
        ]
    if result == "written":
        return [f"Wrote {CONFIG_RELPATH}"]
    return []


def _local_repo_block(checks: list[CheckDef], fail_on: Literal["all", "error"]) -> str:
    hooks = [_hook("byor-ast-grep", "ast-grep scan", entry=ast_grep_entry(fail_on), extensions=[])]
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
