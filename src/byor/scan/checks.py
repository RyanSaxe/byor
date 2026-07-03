"""Run configured BYOR check commands.

Checks extend ast-grep with linters, type checkers, and project scripts that accept file arguments.
This module merges check definitions by precedence, applies exclusions, expands safe home paths, and
records failures for agent feedback.

Each command is `shlex.split` and run directly, never through a shell: that is what keeps a
committed check string from being a shell-injection vector, so there is no `&&`, pipe, redirection,
or alias — multi-step logic belongs in a script the check points at. The in-scope files are appended
as trailing arguments, so a check command must accept a list of file paths.
"""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from byor.config import (
    CheckDef,
    GlobalConfig,
    LocalConfig,
    RepoConfig,
    load_global_config,
    load_local_config,
    load_package_checks,
    load_repo_config,
    repo_config_path,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = (
    "CheckOutcome",
    "EffectiveCheck",
    "effective_checks",
    "load_effective_checks",
    "run_checks",
)


@dataclass(frozen=True)
class EffectiveCheck:
    definition: CheckDef
    origin: str
    """"repo" or "global"."""

    @property
    def name(self) -> str:
        return self.definition.name


@dataclass
class CheckOutcome:
    failures: list[str] = field(default_factory=list)
    """`### <name>` sections for checks that exited nonzero."""
    warnings: list[str] = field(default_factory=list)
    """One line per check whose command could not be found."""


def effective_checks(
    repo_config: RepoConfig,
    global_config: GlobalConfig,
    *,
    local_config: LocalConfig,
    package_checks: Sequence[tuple[str, CheckDef]] = (),
) -> list[EffectiveCheck]:
    """Merge checks by precedence (repo > package > global), minus exclusions.

    The first tier to claim a name wins; lower tiers drop that name. `origin`
    records where the surviving check came from ("repo", "global", or
    "package:<name>"). Excluded names or tags are removed from the result.
    """
    excluded = set(local_config.excluded_checks)
    excluded_tags = set(local_config.excluded_check_tags)
    tiers: list[tuple[str, CheckDef]] = [
        *(("repo", check) for check in repo_config.checks),
        *package_checks,
        *(("global", check) for check in global_config.checks),
    ]
    seen: set[str] = set()
    result: list[EffectiveCheck] = []
    for origin, check in tiers:
        if check.name in seen:
            continue
        seen.add(check.name)
        if check.name in excluded or excluded_tags.intersection(check.tags):
            continue
        result.append(EffectiveCheck(definition=check, origin=origin))
    return result


def load_effective_checks(repo_root: Path, config_dir: Path) -> list[EffectiveCheck]:
    """Load the repo, global, and local configs and merge into effective checks.

    The single I/O wrapper around the pure `effective_checks`; every surface
    (`agent-check`, `list`, `doctor`) routes through it. User-owned global checks
    are personal standards that apply in every repo, so they load even when the
    repo is not byor-initialized; repo checks load only when `.byor/config.yml`
    exists. `.byor/local.yml` exclusions are honored either way.
    """
    repo_config = load_repo_config(repo_root) if repo_config_path(repo_root).is_file() else RepoConfig()
    global_config = load_global_config(config_dir)
    local_config = load_local_config(repo_root)
    package_checks = [
        (f"package:{name}", check)
        for name in local_config.packages
        for check in load_package_checks(config_dir, global_config, name=name)
    ]
    return effective_checks(repo_config, global_config, local_config=local_config, package_checks=package_checks)


def run_checks(
    checks: list[EffectiveCheck],
    repo_root: Path,
    *,
    files: list[Path],
    whole_repo: bool = False,
) -> CheckOutcome:
    """Run each check whose extensions match an in-scope file.

    `whole_repo` is the no-`--files` `agent-check` invocation: every check runs
    once with no file arguments, so the command scans the repository itself
    (e.g. a bare `ruff check`), mirroring ast-grep's whole-repo scan.
    """
    outcome = CheckOutcome()
    for check in checks:
        if whole_repo:
            _run_one(check, repo_root, files=[], outcome=outcome)
            continue
        matching = _matching_files(check.definition, files)
        if not matching:
            continue
        _run_one(check, repo_root, files=matching, outcome=outcome)
    return outcome


def _run_one(check: EffectiveCheck, repo_root: Path, *, files: list[Path], outcome: CheckOutcome) -> None:
    command = [_expand_home(token) for token in shlex.split(check.definition.run)]
    argv = command + [str(file) for file in files]
    try:
        # Check output is displayed, not round-tripped: decode as UTF-8 with
        # "replace" so a non-UTF-8 byte (or a non-UTF-8 Windows locale) cannot
        # raise UnicodeDecodeError past the OSError handler below.
        result = subprocess.run(
            argv,
            cwd=repo_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as error:
        outcome.warnings.append(f"byor: check '{check.name}' could not run ({argv[0]}): {error}")
        return
    if result.returncode != 0:
        outcome.failures.append(_section(check.name, result.stdout, stderr=result.stderr))


def _expand_home(command_part: str) -> str:
    """Expand a leading ``~``/``~/`` to the user's home; leave any other token literal.

    Checks run without a shell (see the module docstring), so byor expands the
    home shorthand itself — but only the unambiguous home forms, never a
    mid-token ``~`` or a ``~user`` that might be meant literally. This is what
    lets a check point ``run`` at a script under ``~/.config/byor`` and have it
    resolve in every repo.
    """
    if command_part == "~" or command_part.startswith("~/"):
        return str(Path(command_part).expanduser())
    return command_part


def _matching_files(check: CheckDef, files: list[Path]) -> list[Path]:
    if not check.extensions:
        return files
    suffixes = {f".{extension.lstrip('.')}" for extension in check.extensions}
    return [file for file in files if file.suffix in suffixes]


def _section(name: str, stdout: str, *, stderr: str) -> str:
    body = "\n".join(part for part in (stdout.rstrip(), stderr.rstrip()) if part)
    return f"### {name}\n{body}" if body else f"### {name}"
