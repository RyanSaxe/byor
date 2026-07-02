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
    RepoConfig,
    load_repo_config,
    repo_config_path,
    save_repo_config,
)
from byor.errors import ByorError
from byor.io.fsio import MANAGED_NOTICE, marked_text_status, write_text_atomic
from byor.io.gitio import default_branch
from byor.rules.sync import load_canonical_rules, mirror_contents, sync_repo
from byor.scaffold.ci import WORKFLOW_RELPATH, workflow_text, write_ci_workflow
from byor.scaffold.githooks import install_precommit_shim
from byor.scaffold.precommit import (
    CONFIG_RELPATH,
    GATE_MARKER,
    precommit_config_text,
    write_precommit_config,
)
from byor.scan.checks import load_effective_checks

__all__ = (
    "heal_gate",
    "install_gate",
    "promote_everything",
    "referenced_vendored_scripts",
    "regenerate_gate",
    "stale_gate_files",
    "vendored_script_problems",
)

VENDORED_SCRIPTS_DIR = ".byor/scripts"
HOME_SCRIPTS_SOURCE_DIR = "~/.config/byor/scripts"
HOME_SCRIPT_PATTERN = re.compile(
    r"(?P<path>(?:\$\{HOME\}|\$HOME|~)/\.config/byor/scripts/"
    r"(?P<name>[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*))"
)

# The provenance marker vendored into each script records where the copy came
# from; removing it hands ownership of the copy to the user.
VENDOR_NOTICE = "Vendored by BYOR from"
VENDOR_MARKER_PATTERN = re.compile(rf"{re.escape(VENDOR_NOTICE)} (?P<source>.+?)\. {re.escape(MANAGED_NOTICE)}")
_COMMENT_PREFIXES = {".cjs": "//", ".js": "//", ".lua": "--", ".mjs": "//", ".ts": "//"}


def install_gate(repo_root: Path, config_dir: Path, *, private: bool) -> list[str]:
    if private:
        return install_precommit_shim(repo_root)
    messages = promote_everything(repo_root, config_dir)
    repo_config = load_repo_config(repo_root)
    repo_config.gate = True
    # Record the branch once so regeneration never flaps across checkouts.
    repo_config.gate_branch = default_branch(repo_root)
    save_repo_config(repo_root, repo_config)
    return messages + regenerate_gate(repo_root)


def regenerate_gate(repo_root: Path) -> list[str]:
    repo_config = load_repo_config(repo_root)
    checks = repo_config.checks
    messages = _revendor_scripts(repo_root, checks)
    branch = _gate_branch(repo_root, repo_config)
    return (
        messages
        + write_ci_workflow(repo_root, checks, default_branch=branch)
        + write_precommit_config(repo_root, checks)
    )


# Relpaths under .byor/scripts that the checks' run commands reference, deduplicated.
def referenced_vendored_scripts(checks: list[CheckDef]) -> list[str]:
    relpaths = [
        token for check in checks for token in shlex.split(check.run) if token.startswith(f"{VENDORED_SCRIPTS_DIR}/")
    ]
    return list(dict.fromkeys(relpaths))


def vendored_script_problems(repo_root: Path, relpath: str) -> list[str]:
    """Read-only findings for one vendored script a repo check runs.

    A missing or non-executable copy breaks the committed gate for every
    contributor. Drift against the recorded source is only reported when that
    source exists on this machine; a copy without the provenance marker is
    user-owned and never compared.
    """
    path = repo_root / relpath
    if not path.is_file():
        return [f"{relpath} is missing; restore it or drop its check from .byor/config.yml"]
    problems: list[str] = []
    if not os.access(path, os.X_OK):
        problems.append(f"{relpath} is not executable; run `chmod +x {relpath}`")
    source = _vendored_source(path)
    if source is not None and source.is_file():
        expected = _expected_vendored_text(path.name, source)
        if expected is not None and path.read_text(encoding="utf-8") != expected:
            problems.append(f"{relpath} drifted from {_source_display(source.resolve())}; run `byor init --gate`")
    return problems


def _revendor_scripts(repo_root: Path, checks: list[CheckDef]) -> list[str]:
    """Refresh vendored scripts whose recorded source changed on this machine.

    Every script a repo check references is compared against the source its
    provenance marker records. A copy without a marker is user-owned, and a
    recorded source that does not exist belongs to a teammate's machine: both
    are left alone. Returns one "Re-vendored ..." message per updated script.
    """
    vendored: dict[str, Path] = {}
    messages: list[str] = []
    for relpath in referenced_vendored_scripts(checks):
        destination = repo_root / relpath
        if not destination.is_file():
            continue
        source = _vendored_source(destination)
        if source is None or not source.is_file():
            continue
        before = destination.read_text(encoding="utf-8")
        _vendor_script(repo_root, source, vendored=vendored)
        if destination.read_text(encoding="utf-8") != before:
            messages.append(f"Re-vendored {relpath}")
    return messages


def stale_gate_files(repo_root: Path, repo_config: RepoConfig) -> list[str]:
    """Gate file relpaths whose content drifted from the configured checks, without writing.

    Doctor uses this to report a stale gate instead of repairing it; the
    comparison renders exactly what regenerate_gate would write and diffs it
    against disk. A gate file the user rewrote without the BYOR marker is
    user-owned — regeneration leaves it alone, so it is not stale either.
    """
    checks = repo_config.checks
    desired = {
        WORKFLOW_RELPATH: workflow_text(checks, default_branch=_gate_branch(repo_root, repo_config)),
        CONFIG_RELPATH: precommit_config_text(checks),
    }
    return [
        relpath
        for relpath, content in desired.items()
        if marked_text_status(repo_root / relpath, content, marker=GATE_MARKER) in ("missing", "drifted")
    ]


def _gate_branch(repo_root: Path, repo_config: RepoConfig) -> str:
    # Configs from before the branch was recorded fall back to detection.
    return repo_config.gate_branch or default_branch(repo_root)


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
        if strip_package and _exists_with_other_content(destination, content):
            # Two packages shipping the same filename must both be vendored,
            # so the loser keeps its package prefix instead of being dropped.
            destination = project_dir / relpath
        if not destination.exists():
            write_text_atomic(destination, content)
            written += 1
    return written


def _exists_with_other_content(destination: Path, content: str) -> bool:
    return destination.is_file() and destination.read_text(encoding="utf-8") != content


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
    The copy carries a provenance marker line recording the source, so heal can
    re-vendor when the source changes; a copy whose marker was removed is
    user-owned and never rewritten.
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
        # Binary scripts cannot carry a text marker; copy them wholesale.
        shutil.copyfile(source, destination)
    else:
        _vendor_dependencies(repo_root, content, vendored=vendored)
        text = _marked_vendored_text(
            _rewrite_home_script_refs(content),
            name=destination.name,
            source_display=_source_display(resolved),
        )
        _write_unless_user_owned(destination, text)
    destination.chmod(destination.stat().st_mode | 0o111)
    return relpath


def _write_unless_user_owned(destination: Path, text: str) -> None:
    if destination.is_file():
        existing = destination.read_text(encoding="utf-8")
        if VENDOR_NOTICE not in existing:
            # The marker was removed (or never written): the user owns this copy.
            return
        if existing == text:
            return
    write_text_atomic(destination, text)


def _marked_vendored_text(text: str, *, name: str, source_display: str) -> str:
    prefix = _COMMENT_PREFIXES.get(Path(name).suffix, "#")
    marker = f"{prefix} {VENDOR_NOTICE} {source_display}. {MANAGED_NOTICE}"
    if text.startswith("#!"):
        shebang, _, rest = text.partition("\n")
        return f"{shebang}\n{marker}\n{rest}"
    return f"{marker}\n{text}"


# The provenance a vendored script records, or None for user-owned/binary copies.
def _vendored_source(path: Path) -> Path | None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    match = VENDOR_MARKER_PATTERN.search(text)
    if match is None:
        return None
    return Path(match.group("source")).expanduser()


# What vendoring `source` would write, or None for binary sources.
def _expected_vendored_text(name: str, source: Path) -> str | None:
    try:
        content = source.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None
    return _marked_vendored_text(
        _rewrite_home_script_refs(content),
        name=name,
        source_display=_source_display(source.resolve()),
    )


def _source_display(resolved: Path) -> str:
    # Recorded `~/`-relative so the marker is meaningful on teammate machines.
    try:
        return f"~/{resolved.relative_to(Path.home().resolve()).as_posix()}"
    except ValueError:
        return str(resolved)


def _vendored_name(resolved: Path) -> str:
    # A script under ~/.config/byor/scripts/ keeps its subpath so nested
    # references stay portable; anything else flattens to its basename.
    scripts_home = Path(HOME_SCRIPTS_SOURCE_DIR).expanduser().resolve()
    try:
        return resolved.relative_to(scripts_home).as_posix()
    except ValueError:
        return resolved.name


# Vendor every existing home script `content` references, recursively.
def _vendor_dependencies(repo_root: Path, content: str, *, vendored: dict[str, Path]) -> None:
    for match in HOME_SCRIPT_PATTERN.finditer(content):
        source = _referenced_script(match)
        if source is not None:
            _vendor_script(repo_root, source, vendored=vendored)


# Pure rewrite of home-script references to their vendored relpaths, so the
# expected text of a vendored script can be computed without writing anything.
def _rewrite_home_script_refs(content: str) -> str:
    def replace_match(match: re.Match[str]) -> str:
        source = _referenced_script(match)
        if source is None:
            return match.group("path")
        return f"{VENDORED_SCRIPTS_DIR}/{_vendored_name(source.resolve())}"

    return HOME_SCRIPT_PATTERN.sub(replace_match, content)


def _referenced_script(match: re.Match[str]) -> Path | None:
    source = Path(os.path.expandvars(match.group("path"))).expanduser()
    return source if source.is_file() else None
