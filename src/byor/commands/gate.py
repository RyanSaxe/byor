"""Distribute a blocking gate: promote everything, then emit byor-free artifacts.

A shared gate first vendors every effective rule and check into tracked config
(project rules and .byor/config.yml), rewriting any check that points at a
`~/` script to a copy committed under .byor/scripts/. The emitted
.pre-commit-config.yaml and CI workflow then run `ast-grep scan --error` plus
those checks directly — no byor, no `~/.config/byor`. A private gate commits
nothing and installs a local pre-commit shim instead.
"""

from __future__ import annotations

import os
import shlex
import shutil
from dataclasses import replace
from pathlib import Path

from byor.config import (
    CheckDef,
    load_repo_config,
    repo_config_path,
    save_repo_config,
)
from byor.io.fsio import write_text_atomic
from byor.rules.sync import load_canonical_rules, mirror_contents, sync_repo
from byor.scaffold.ci import write_ci_workflow
from byor.scaffold.githooks import install_precommit_shim
from byor.scaffold.precommit import write_precommit_config
from byor.scan.checks import load_effective_checks

VENDORED_SCRIPTS_DIR = ".byor/scripts"


def install_gate(repo_root: Path, config_dir: Path, private: bool) -> list[str]:
    """Install the team gate; private repos get only a local, uncommitted shim."""
    if private:
        return install_precommit_shim(repo_root)
    messages = promote_everything(repo_root, config_dir)
    repo_config = load_repo_config(repo_root)
    repo_config.gate = True
    save_repo_config(repo_root, repo_config)
    return messages + regenerate_gate(repo_root)


def regenerate_gate(repo_root: Path) -> list[str]:
    """(Re)write the byor-free pre-commit and CI artifacts from the committed config.

    Both are byor-owned build products: `write_marked_text` overwrites them when
    they carry byor's marker and leaves a user-owned pre-commit config alone.
    """
    checks = load_repo_config(repo_root).checks
    return write_ci_workflow(repo_root, checks) + write_precommit_config(
        repo_root, checks
    )


def heal_gate(repo_root: Path) -> list[str]:
    """Keep a gated repo's artifacts current; a no-op without an initialized gate."""
    if not repo_config_path(repo_root).is_file():
        return []
    if not load_repo_config(repo_root).gate:
        return []
    return regenerate_gate(repo_root)


def promote_everything(repo_root: Path, config_dir: Path) -> list[str]:
    """Vendor every effective rule into project rules and every check into repo config.

    Rules come straight from the two mirrors, which already hold exactly the
    effective set; the follow-up sync then clears them since project owns the
    IDs. Checks an owned scope already provides (repo checks) are left alone.
    """
    paths = load_repo_config(repo_root).paths
    project_dir = repo_root / paths.project_rules
    rules = _copy_mirror(project_dir, repo_root / paths.personal_global_rules, False)
    rules += _copy_mirror(project_dir, repo_root / paths.personal_packages_rules, True)

    repo_config = load_repo_config(repo_root)
    names = {check.name for check in repo_config.checks}
    promoted_checks = 0
    for effective in load_effective_checks(repo_root, config_dir):
        if effective.origin == "repo" or effective.name in names:
            continue
        repo_config.checks.append(_vendor_check(repo_root, effective.definition))
        names.add(effective.name)
        promoted_checks += 1
    save_repo_config(repo_root, repo_config)
    sync_repo(repo_root, load_canonical_rules(config_dir))
    return [f"Promoted {rules} rules and {promoted_checks} checks into tracked config"]


def _copy_mirror(project_dir: Path, mirror_dir: Path, strip_package: bool) -> int:
    """Copy each mirrored rule into project rules; return how many were written.

    Package copies live under `<package>/...`; that leading segment is dropped so
    the rule lands at the same path a `byor promote --from package` would use.
    """
    written = 0
    for relpath, content in mirror_contents(mirror_dir).items():
        dest_rel = (
            relpath.split("/", 1)[1] if strip_package and "/" in relpath else relpath
        )
        destination = project_dir / dest_rel
        if not destination.exists():
            write_text_atomic(destination, content)
            written += 1
    return written


def _vendor_check(repo_root: Path, check: CheckDef) -> CheckDef:
    """Copy any `~/` script the check runs into the repo and repoint `run` at it.

    A check whose command lives under the home directory cannot run in CI or on
    a teammate's machine; vendoring the script keeps the committed gate portable.
    """
    tokens = shlex.split(check.run)
    rewritten: list[str] = []
    changed = False
    for token in tokens:
        source = Path(os.path.expanduser(token)) if token.startswith("~/") else None
        if source is not None and source.is_file():
            rewritten.append(_vendor_script(repo_root, source))
            changed = True
        else:
            rewritten.append(token)
    return replace(check, run=shlex.join(rewritten)) if changed else check


def _vendor_script(repo_root: Path, source: Path) -> str:
    destination = repo_root / VENDORED_SCRIPTS_DIR / source.name
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    destination.chmod(destination.stat().st_mode | 0o111)
    return f"{VENDORED_SCRIPTS_DIR}/{source.name}"
