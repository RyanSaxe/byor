"""Extra command-line checks run after ast-grep diagnostics.

`checks:` entries in the repo and global configs name a command to run on the
in-scope files whose extension matches. Repo checks win over global ones by
name; `.byor/local.yml` `checks.excluded` disables them per repo. A failing
check's raw output is appended under a `### <name>` header and yields the same
diagnostics-exist exit code as an ast-grep finding. A missing command warns
once and never crashes the hook — committed checks run on contributors'
machines under the same trust model as pre-commit.
"""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from byor.config import (
    CheckDef,
    GlobalConfig,
    LocalConfig,
    RepoConfig,
    load_global_config,
    load_local_config,
    load_repo_config,
    repo_config_path,
)


@dataclass(frozen=True)
class EffectiveCheck:
    """A check after merge and exclusion, tagged with where it came from."""

    definition: CheckDef
    origin: str
    """"repo" or "global"."""

    @property
    def name(self) -> str:
        return self.definition.name


@dataclass
class CheckOutcome:
    """The result of running the matching checks over a set of files."""

    failures: list[str] = field(default_factory=list)
    """`### <name>` sections for checks that exited nonzero."""
    warnings: list[str] = field(default_factory=list)
    """One line per check whose command could not be found."""


def effective_checks(
    repo_config: RepoConfig, global_config: GlobalConfig, local_config: LocalConfig
) -> list[EffectiveCheck]:
    """Merge repo and global checks by name (repo wins), minus local exclusions.

    Repo checks keep their config order; global checks whose name a repo check
    already claims are dropped. Excluded names are removed from the result.
    """
    excluded = set(local_config.excluded_checks)
    repo_names = {check.name for check in repo_config.checks}
    merged = list(repo_config.checks) + [
        check for check in global_config.checks if check.name not in repo_names
    ]
    origins = {check.name: "repo" for check in repo_config.checks}
    return [
        EffectiveCheck(definition=check, origin=origins.get(check.name, "global"))
        for check in merged
        if check.name not in excluded
    ]


def load_effective_checks(repo_root: Path, config_dir: Path) -> list[EffectiveCheck]:
    """Load the repo, global, and local configs and merge into effective checks.

    The single I/O wrapper around the pure `effective_checks`; every surface
    (`agent-check`, `list`, `doctor`) routes through it. User-owned global checks
    are personal standards that apply in every repo, so they load even when the
    repo is not byor-initialized; repo checks load only when `.byor/config.yml`
    exists. `.byor/local.yml` exclusions are honored either way.
    """
    repo_config = (
        load_repo_config(repo_root)
        if repo_config_path(repo_root).is_file()
        else RepoConfig()
    )
    return effective_checks(
        repo_config,
        load_global_config(config_dir),
        load_local_config(repo_root),
    )


def run_checks(
    checks: list[EffectiveCheck], repo_root: Path, files: list[Path]
) -> CheckOutcome:
    """Run each check whose extensions match an in-scope file."""
    outcome = CheckOutcome()
    for check in checks:
        matching = _matching_files(check.definition, files)
        if not matching:
            continue
        _run_one(check, repo_root, matching, outcome)
    return outcome


def _run_one(
    check: EffectiveCheck, repo_root: Path, files: list[Path], outcome: CheckOutcome
) -> None:
    argv = shlex.split(check.definition.run) + [str(file) for file in files]
    try:
        result = subprocess.run(
            argv, cwd=repo_root, capture_output=True, text=True, check=False
        )
    except FileNotFoundError:
        outcome.warnings.append(
            f"byor: check '{check.name}' command not found: {argv[0]}"
        )
        return
    if result.returncode != 0:
        outcome.failures.append(_section(check.name, result.stdout, result.stderr))


def _matching_files(check: CheckDef, files: list[Path]) -> list[Path]:
    if not check.extensions:
        return files
    suffixes = {f".{extension.lstrip('.')}" for extension in check.extensions}
    return [file for file in files if file.suffix in suffixes]


def _section(name: str, stdout: str, stderr: str) -> str:
    body = "\n".join(part for part in (stdout.rstrip(), stderr.rstrip()) if part)
    return f"### {name}\n{body}" if body else f"### {name}"
