"""Generate repository gate scaffolding from BYOR configuration.

Gate installation vendors project rules and check scripts into committed pre-commit and CI files.
The module keeps generated enforcement reproducible so users can dogfood BYOR locally and in GitHub
Actions.
"""

from __future__ import annotations

import os
import re
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
from byor.errors import ByorError
from byor.io.fsio import write_text_atomic
from byor.rules.sync import load_canonical_rules, mirror_contents, sync_repo
from byor.scaffold.ci import write_ci_workflow
from byor.scaffold.githooks import install_precommit_shim
from byor.scaffold.precommit import write_precommit_config
from byor.scan.checks import load_effective_checks

__all__ = (
    "heal_gate",
    "install_gate",
    "promote_everything",
    "regenerate_gate",
)

VENDORED_SCRIPTS_DIR = ".byor/scripts"
HOME_SCRIPTS_SOURCE_DIR = "~/.config/byor/scripts"
HOME_SCRIPT_PATTERN = re.compile(
    r"(?P<path>(?:\$\{HOME\}|\$HOME|~)/\.config/byor/scripts/"
    r"(?P<name>[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*))"
)


def install_gate(repo_root: Path, config_dir: Path, *, private: bool) -> list[str]:
    if private:
        return install_precommit_shim(repo_root)
    messages = promote_everything(repo_root, config_dir)
    repo_config = load_repo_config(repo_root)
    repo_config.gate = True
    save_repo_config(repo_root, repo_config)
    return messages + regenerate_gate(repo_root)


def regenerate_gate(repo_root: Path) -> list[str]:
    checks = load_repo_config(repo_root).checks
    return write_ci_workflow(repo_root, checks) + write_precommit_config(repo_root, checks)


def heal_gate(repo_root: Path) -> list[str]:
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
    rules = _copy_mirror(project_dir, repo_root / paths.personal_global_rules, strip_package=False)
    rules += _copy_mirror(project_dir, repo_root / paths.personal_packages_rules, strip_package=True)

    repo_config = load_repo_config(repo_root)
    names = {check.name for check in repo_config.checks}
    promoted_checks = 0
    vendored: dict[str, Path] = {}
    for effective in load_effective_checks(repo_root, config_dir):
        if effective.origin == "repo" or effective.name in names:
            continue
        repo_config.checks.append(_vendor_check(repo_root, effective.definition, vendored=vendored))
        names.add(effective.name)
        promoted_checks += 1
    save_repo_config(repo_root, repo_config)
    sync_repo(repo_root, load_canonical_rules(config_dir))
    return [f"Promoted {rules} rules and {promoted_checks} checks into tracked config"]


def _copy_mirror(project_dir: Path, mirror_dir: Path, *, strip_package: bool) -> int:
    written = 0
    for relpath, content in mirror_contents(mirror_dir).items():
        dest_rel = relpath.split("/", 1)[1] if strip_package and "/" in relpath else relpath
        destination = project_dir / dest_rel
        if not destination.exists():
            write_text_atomic(destination, content)
            written += 1
    return written


def _vendor_check(repo_root: Path, check: CheckDef, *, vendored: dict[str, Path]) -> CheckDef:
    tokens = shlex.split(check.run)
    rewritten: list[str] = []
    changed = False
    for token in tokens:
        source = Path(token).expanduser() if token.startswith("~/") else None
        if source is not None and source.is_file():
            rewritten.append(_vendor_script(repo_root, source, vendored=vendored))
            changed = True
        else:
            rewritten.append(token)
    return replace(check, run=shlex.join(rewritten)) if changed else check


def _vendor_script(repo_root: Path, source: Path, *, vendored: dict[str, Path]) -> str:
    """Copy `source` into the repo's vendored scripts dir and return its relpath.

    `vendored` maps each destination relpath to the source that produced it: it
    makes re-vendoring the same script idempotent (and recursion-safe) while a
    second, different source claiming the same destination is a hard error.
    """
    resolved = source.resolve()
    relpath = f"{VENDORED_SCRIPTS_DIR}/{_vendored_name(resolved)}"
    already = vendored.get(relpath)
    if already == resolved:
        return relpath
    if already is not None:
        msg = f"cannot vendor {source} to {relpath}: {already} is already vendored there; rename one of the scripts"
        raise ByorError(msg)
    vendored[relpath] = resolved
    destination = repo_root / relpath
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        content = source.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        shutil.copyfile(source, destination)
    else:
        content = _rewrite_vendored_script_content(repo_root, content, vendored=vendored)
        write_text_atomic(destination, content)
    destination.chmod(destination.stat().st_mode | 0o111)
    return relpath


def _vendored_name(resolved: Path) -> str:
    # A script under ~/.config/byor/scripts/ keeps its subpath so nested
    # references stay portable; anything else flattens to its basename.
    scripts_home = Path(HOME_SCRIPTS_SOURCE_DIR).expanduser().resolve()
    try:
        return resolved.relative_to(scripts_home).as_posix()
    except ValueError:
        return resolved.name


def _rewrite_vendored_script_content(repo_root: Path, content: str, *, vendored: dict[str, Path]) -> str:
    def replace_match(match: re.Match[str]) -> str:
        raw_path = match.group("path")
        source = Path(os.path.expandvars(raw_path)).expanduser()
        if not source.is_file():
            return raw_path
        return _vendor_script(repo_root, source, vendored=vendored)

    return HOME_SCRIPT_PATTERN.sub(replace_match, content)
