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
    load_local_config,
    load_repo_config,
    repo_config_path,
    save_local_config,
    save_repo_config,
)
from byor.errors import ByorError, ConfigError
from byor.io.fsio import MANAGED_NOTICE, marked_text_status, write_text_atomic
from byor.io.gitio import default_branch, git_output
from byor.io.yamlio import parse_yaml_mapping
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
    "directly_invoked_vendored_scripts",
    "heal_gate",
    "install_gate",
    "precommit_hook_installed",
    "promote_everything",
    "referenced_vendored_scripts",
    "regenerate_gate",
    "stale_gate_files",
    "transitive_vendored_scripts",
    "vendor_check",
    "vendored_script_problems",
)

# `byor init --gate` writes the pre-commit config, but only `pre-commit
# install` makes it run locally — without this line a green-looking gate
# silently enforces nothing on commit.
PRECOMMIT_INSTALL_HINT = (
    "The local pre-commit gate is inactive until you run `uvx pre-commit install` (CI still enforces)"
)


def precommit_hook_installed(repo_root: Path) -> bool | None:
    """Whether a pre-commit hook file exists in the repo's git hooks directory.

    Any hook file present is assumed active — inspecting its internals to
    prove it is really the pre-commit framework's would be guesswork. None
    means there is no hooks directory to look in (not a git repo).
    """
    if not (repo_root / ".git").exists():
        return None
    hooks = git_output(repo_root, "rev-parse", "--git-path", "hooks")
    if hooks is None:
        return None
    return ((repo_root / hooks) / "pre-commit").is_file()


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
    # Record the branch once so regeneration never flaps across checkouts —
    # a re-run (say, from doctor's remediation) must not re-detect it from
    # whatever feature branch happens to be checked out.
    if repo_config.gate_branch is None:
        repo_config.gate_branch = default_branch(repo_root)
    save_repo_config(repo_root, repo_config)
    messages += regenerate_gate(repo_root)
    if precommit_hook_installed(repo_root) is not True:
        messages.append(PRECOMMIT_INSTALL_HINT)
    return messages


def regenerate_gate(repo_root: Path) -> list[str]:
    repo_config = load_repo_config(repo_root)
    checks = repo_config.checks
    messages = _revendor_scripts(repo_root, checks)
    branch = _gate_branch(repo_root, repo_config)
    gated = _gated_checks(checks)
    return (
        messages
        + write_ci_workflow(repo_root, gated, default_branch=branch, fail_on=repo_config.fail_on)
        + write_precommit_config(repo_root, gated, fail_on=repo_config.fail_on)
    )


# An agent-only check (gate: false) stays in repo config so every teammate's
# hook runs it, but the committed pre-commit and CI files must not: it polices
# agents, and in a gate it would block humans doing the same thing on purpose.
def _gated_checks(checks: list[CheckDef]) -> list[CheckDef]:
    return [check for check in checks if check.gate]


# Relpaths under .byor/scripts that the checks' run commands reference, deduplicated.
def referenced_vendored_scripts(checks: list[CheckDef]) -> list[str]:
    relpaths = [
        token for check in checks for token in shlex.split(check.run) if token.startswith(f"{VENDORED_SCRIPTS_DIR}/")
    ]
    return list(dict.fromkeys(relpaths))


# Vendored scripts a check invokes as its command's argv[0]. Only these need
# the exec bit: interpreter arguments (`sh .byor/scripts/x.sh`) run without it.
def directly_invoked_vendored_scripts(checks: list[CheckDef]) -> set[str]:
    argv0s = {shlex.split(check.run)[0] for check in checks}
    return {token for token in argv0s if token.startswith(f"{VENDORED_SCRIPTS_DIR}/")}


# A `.byor/scripts/...` reference inside a vendored script's own text, the shape
# _rewrite_home_script_refs produces when it vendors a script's dependencies.
VENDORED_REF_PATTERN = re.compile(rf"{re.escape(VENDORED_SCRIPTS_DIR)}/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*")


def transitive_vendored_scripts(repo_root: Path, relpaths: list[str]) -> list[str]:
    """`relpaths` plus every vendored script their text references, recursively.

    Doctor needs the full closure: a vendored runner that calls a second
    vendored script breaks the gate just as hard when that dependency goes
    missing, even though no check's run command names it directly. Unreadable
    or binary scripts contribute no references.
    """
    closure: dict[str, None] = {}
    pending = list(relpaths)
    while pending:
        relpath = pending.pop(0)
        if relpath in closure:
            continue
        closure[relpath] = None
        pending.extend(_vendored_references(repo_root / relpath))
    return list(closure)


def _vendored_references(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    return VENDORED_REF_PATTERN.findall(text)


def vendored_script_problems(repo_root: Path, relpath: str, *, executable_required: bool = True) -> list[str]:
    """Read-only findings for one vendored script a repo check runs.

    A missing copy breaks the committed gate for every contributor. The exec
    bit is required only when the script is invoked directly (`executable_
    required`): interpreter arguments and scripts called from other scripts
    run fine without it. Drift is only reported for a copy this checkout
    vendored (see `LocalConfig.vendored_scripts`) whose source exists here; a
    copy without the provenance marker is user-owned and never compared.
    """
    path = repo_root / relpath
    if not path.is_file():
        return [f"{relpath} is missing; restore it or drop its check from .byor/config.yml"]
    problems: list[str] = []
    if executable_required and not os.access(path, os.X_OK):
        # chmod fixes this machine; the index mode is what teammates check out.
        problems.append(
            f"{relpath} is not executable; run `chmod +x {relpath}` and `git update-index --chmod=+x {relpath}`"
        )
    source = _vendored_source(path)
    if source is not None and source.is_file() and relpath in _recorded_vendored_scripts(repo_root):
        expected = _expected_vendored_text(path.name, source, repo_root=repo_root)
        if expected is not None and path.read_text(encoding="utf-8") != expected:
            problems.append(f"{relpath} drifted from {_source_display(source)}; run `byor init --gate`")
    return problems


def _revendor_scripts(repo_root: Path, checks: list[CheckDef]) -> list[str]:
    """Refresh vendored scripts whose recorded source changed on this machine.

    Only copies this checkout vendored are refreshed, from the source their
    provenance marker records. A same-named script in a teammate's home is a
    different file, a copy without a marker is user-owned, and a recorded source
    that does not exist here belongs to another machine: all are left alone.
    Returns one "Re-vendored ..." message per updated script.
    """
    vendored: dict[str, Path] = {}
    messages: list[str] = []
    recorded = _recorded_vendored_scripts(repo_root)
    for relpath in referenced_vendored_scripts(checks):
        if relpath not in recorded:
            continue
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
    checks = _gated_checks(repo_config.checks)
    desired = {
        WORKFLOW_RELPATH: workflow_text(
            checks, default_branch=_gate_branch(repo_root, repo_config), fail_on=repo_config.fail_on
        ),
        CONFIG_RELPATH: precommit_config_text(checks, fail_on=repo_config.fail_on),
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
    """Regenerate the gate files before a command runs, degrading to a warning.

    Like heal_repo, a broken repo config (or a vendoring collision) must not
    block the unrelated command — raising here would also discard the warnings
    the earlier heal steps already collected.
    """
    if not repo_config_path(repo_root).is_file():
        return []
    try:
        if not load_repo_config(repo_root).gate:
            return []
        return regenerate_gate(repo_root)
    except ByorError as error:
        return [f"byor: skipping gate self-heal: {error} (run 'byor doctor')"]


def promote_everything(repo_root: Path, config_dir: Path) -> list[str]:
    """Vendor every effective rule into project rules and every check into repo config.

    Rules come straight from the two mirrors, which already hold exactly the
    effective set; the follow-up sync then clears them since project owns the
    IDs. Repo checks keep their name and place, but any `~/...` script they
    run is vendored like a global check's: the committed gate would otherwise
    embed a home path no teammate machine or CI runner has.
    """
    paths = load_repo_config(repo_root).paths
    project_dir = repo_root / paths.project_rules
    rules = _copy_mirror(project_dir, repo_root / paths.personal_global_rules, strip_package=False)
    rules += _copy_mirror(project_dir, repo_root / paths.personal_packages_rules, strip_package=True)

    repo_config = load_repo_config(repo_root)
    vendored: dict[str, Path] = {}
    repo_config.checks = [vendor_check(repo_root, check, vendored=vendored) for check in repo_config.checks]
    names = {check.name for check in repo_config.checks}
    promoted_checks: list[str] = []
    for effective in load_effective_checks(repo_root, config_dir):
        if effective.origin == "repo" or effective.name in names:
            continue
        repo_config.checks.append(vendor_check(repo_root, effective.definition, vendored=vendored))
        names.add(effective.name)
        promoted_checks.append(effective.name)
    save_repo_config(repo_root, repo_config)
    _record_vendored_scripts(repo_root, repo_config.checks)
    sync_repo(repo_root, load_canonical_rules(config_dir))
    rules_part = _promoted_part("rule", rules)
    checks_part = _promoted_part("check", promoted_checks)
    return [f"Promoted {rules_part} and {checks_part} into tracked config"]


# `byor init --gate` is the maintainer's explicit step, so the vendored copies
# its checks reference become this checkout's to keep current from here on.
def _record_vendored_scripts(repo_root: Path, checks: list[CheckDef]) -> None:
    relpaths = transitive_vendored_scripts(repo_root, referenced_vendored_scripts(checks))
    local = load_local_config(repo_root)
    merged = sorted({*local.vendored_scripts, *relpaths})
    if merged != local.vendored_scripts:
        local.vendored_scripts = merged
        save_local_config(repo_root, local)


# A malformed local.yml is reported by doctor's own row; here it only means
# nothing counts as vendored by this checkout.
def _recorded_vendored_scripts(repo_root: Path) -> set[str]:
    try:
        return set(load_local_config(repo_root).vendored_scripts)
    except ConfigError:
        return set()


# "2 rules (a, b)": the count keeps the shape scannable, the ids say exactly
# what init just committed to the repo's tracked config.
def _promoted_part(noun: str, names: list[str]) -> str:
    count = f"{len(names)} {noun}{'' if len(names) == 1 else 's'}"
    return f"{count} ({', '.join(names)})" if names else count


def _copy_mirror(project_dir: Path, mirror_dir: Path, *, strip_package: bool) -> list[str]:
    written: list[str] = []
    for relpath, content in mirror_contents(mirror_dir).items():
        dest_rel = relpath.split("/", 1)[1] if strip_package and "/" in relpath else relpath
        destination = project_dir / dest_rel
        if _exists_with_other_content(destination, content):
            # A filename collision must not silently drop the rule: the loser
            # keeps its package prefix, or a global/ prefix for global rules
            # colliding with a project rule of the same relpath.
            destination = project_dir / (relpath if strip_package else f"global/{relpath}")
        if not destination.exists():
            write_text_atomic(destination, content)
            written.append(_rule_id_of(content, fallback=destination.stem))
    return written


# The rule's declared id; mirrored rules already validated, so the filename
# stem fallback only covers content that decayed on disk since the sync.
def _rule_id_of(content: str, *, fallback: str) -> str:
    try:
        declared = parse_yaml_mapping(content, source=Path("<mirrored rule>")).get("id")
    except ConfigError:
        return fallback
    return declared if isinstance(declared, str) else fallback


def _exists_with_other_content(destination: Path, content: str) -> bool:
    return destination.is_file() and destination.read_text(encoding="utf-8") != content


def vendor_check(repo_root: Path, check: CheckDef, *, vendored: dict[str, Path] | None = None) -> CheckDef:
    """Point a check's `~/` scripts at copies vendored into the repo's `.byor/scripts/`.

    A committed gate runs on teammates' machines and CI runners, where a home path does not exist,
    and pre-commit does not expand `~` at all. `init --gate` vendors every check this way, and
    `byor promote --check` does the same for one check in a gated repo. `vendored` carries the
    destinations already written in one pass, so two checks sharing a script copy it once; omit it
    for a single check. A token naming a missing file is left as written.
    """
    vendored = {} if vendored is None else vendored
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

    `vendored` maps each destination relpath (casefolded, because APFS and NTFS
    would merge Lint.py and lint.py into one file) to the source that produced
    it: it makes re-vendoring the same script idempotent (and recursion-safe)
    while a second, different source claiming the same destination is a hard
    error. The copy carries a provenance marker line recording the source, so
    heal can re-vendor when the source changes; a copy whose marker was removed
    is user-owned and never rewritten.
    """
    resolved = source.resolve()
    relpath = f"{VENDORED_SCRIPTS_DIR}/{_vendored_name(source)}"
    already = vendored.get(relpath.casefold())
    if already == resolved:
        return relpath
    if already is not None:
        msg = f"cannot vendor {source} to {relpath}: {already} is already vendored there; rename one of the scripts"
        raise ByorError(msg)
    vendored[relpath.casefold()] = resolved
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
            _rewrite_home_script_refs(content, repo_root=repo_root),
            name=destination.name,
            source_display=_source_display(source),
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
def _expected_vendored_text(name: str, source: Path, *, repo_root: Path) -> str | None:
    try:
        content = source.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None
    return _marked_vendored_text(
        _rewrite_home_script_refs(content, repo_root=repo_root),
        name=name,
        source_display=_source_display(source),
    )


# The marker and the vendored name use the source path as the check names it,
# symlinks unresolved, so a ~/.config/byor/scripts linked into a store keeps
# naming ~/.config/byor/scripts/... when the store path behind it moves. The
# marker is `~/`-relative so it is meaningful on teammate machines.
def _source_display(located: Path) -> str:
    for home in (Path.home(), Path.home().resolve()):
        if located.is_relative_to(home):
            return f"~/{located.relative_to(home).as_posix()}"
    return str(located)


def _vendored_name(located: Path) -> str:
    # A script under ~/.config/byor/scripts/ keeps its subpath so nested
    # references stay portable; anything else flattens to its basename.
    scripts_home = Path(HOME_SCRIPTS_SOURCE_DIR).expanduser()
    for base in (scripts_home, scripts_home.resolve()):
        if located.is_relative_to(base):
            return located.relative_to(base).as_posix()
    return located.name


# Vendor every existing home script `content` references, recursively.
def _vendor_dependencies(repo_root: Path, content: str, *, vendored: dict[str, Path]) -> None:
    for match in HOME_SCRIPT_PATTERN.finditer(content):
        source = _referenced_script(match)
        if source is not None:
            _vendor_script(repo_root, source, vendored=vendored)


# Pure rewrite of home-script references to their vendored relpaths, so the
# expected text of a vendored script can be computed without writing anything.
def _rewrite_home_script_refs(content: str, *, repo_root: Path) -> str:
    def replace_match(match: re.Match[str]) -> str:
        source = _referenced_script(match)
        if source is not None:
            return f"{VENDORED_SCRIPTS_DIR}/{_vendored_name(source)}"
        # The home source is gone but the vendored copy is committed: keep the
        # rewrite keyed on that stable destination, so drift detection and
        # re-vendoring never resurrect a `~/...` path no teammate machine has.
        relpath = f"{VENDORED_SCRIPTS_DIR}/{match.group('name')}"
        if (repo_root / relpath).is_file():
            return relpath
        return match.group("path")

    return HOME_SCRIPT_PATTERN.sub(replace_match, content)


def _referenced_script(match: re.Match[str]) -> Path | None:
    source = Path(os.path.expandvars(match.group("path"))).expanduser()
    return source if source.is_file() else None
