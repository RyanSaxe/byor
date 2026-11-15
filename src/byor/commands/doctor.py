"""Report actionable installation health for BYOR.

The doctor command checks both machine-level integration state and repository state, then renders a
compact pass/fail report for humans or JSON callers. The checks stay deliberately small so init,
sync, and install flows can reuse the same health model after they mutate BYOR-managed files.
"""

from __future__ import annotations

import json
import os
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from byor.agents.install import AGENT_CHOICES, agent_file_problems, unrecorded_agents
from byor.commands.gate import (
    directly_invoked_vendored_scripts,
    precommit_hook_installed,
    referenced_vendored_scripts,
    stale_gate_files,
    transitive_vendored_scripts,
    user_owned_gate_gaps,
    vendored_script_problems,
)
from byor.commands.package import available_packages
from byor.config import (
    GlobalConfig,
    MachineState,
    RepoConfig,
    RepoPaths,
    command_rule_dir_relpaths,
    global_commands_dir,
    global_rules_dir,
    legacy_machine_state,
    load_global_config,
    load_local_config,
    load_machine_state,
    load_package_checks,
    load_repo_config,
    load_repo_registry,
    local_config_path,
    repo_config_path,
    repo_registry_path,
    rule_dir_relpaths,
)
from byor.errors import (
    AstGrepNotFoundError,
    ByorError,
    ConfigError,
    DuplicateRuleIdError,
    RepoNotInitializedError,
    RuleValidationError,
)
from byor.io.fsio import dangling_link_problem
from byor.io.gitio import git_stdout
from byor.io.output import write_line, write_lines
from byor.io.paths import global_config_dir, global_state_dir, home_sgconfig_path, resolve_repo_root
from byor.io.yamlio import load_yaml_mapping
from byor.rules.rules import check_id_conflicts, load_rules
from byor.rules.sync import (
    load_canonical_rules,
    mirror_contents,
    repo_plans,
)
from byor.scaffold.githooks import shim_findings
from byor.scaffold.ignore import IgnoreBlockStatus, ignore_block_statuses, rule_visibility_ok
from byor.scaffold.precommit import CONFIG_RELPATH
from byor.scaffold.worktree import pending_copy, worktree_disabled_entry
from byor.scan.astgrep import ast_grep_version, config_load_error, resolve_ast_grep, scan_command
from byor.scan.checks import effective_checks

if TYPE_CHECKING:
    import argparse

__all__ = (
    "Check",
    "collect_checks",
    "quick_doctor_problems",
    "render_checks",
    "run_doctor",
)


@dataclass(kw_only=True)
class Check:
    id: str
    ok: bool
    message: str


def run_doctor(args: argparse.Namespace) -> int:
    repo_root = resolve_repo_root(explicit=args.repo)
    checks = collect_checks(repo_root, global_config_dir(), quick=args.quick)
    ok = all(check.ok for check in checks)
    if args.json:
        payload = {"ok": ok, "checks": [asdict(check) for check in checks]}
        write_line(json.dumps(payload, indent=2))
    else:
        write_lines(render_checks(checks))
    return 0 if ok else 1


def collect_checks(repo_root: Path, config_dir: Path, *, quick: bool) -> list[Check]:
    """Collect global and repository health checks.

    A global machine-level section always runs, plus a repo section when run
    inside a byor repo.
    `quick` skips recursive rule validation (rule parsing, ID uniqueness, sync
    staleness) but keeps the cheap checks.
    """
    global_config = load_global_config(config_dir)
    checks = _global_checks(
        config_dir, global_state_dir(), global_config=global_config, current_repo=repo_root, quick=quick
    )
    checks.extend(
        _repo_checks(
            repo_root,
            config_dir,
            global_config=global_config,
            quick=quick,
        )
    )
    return checks


def _global_checks(
    config_dir: Path, state_dir: Path, *, global_config: GlobalConfig, current_repo: Path, quick: bool
) -> list[Check]:
    state = load_machine_state(state_dir)
    checks = [
        _ast_grep_check(global_config),
        _home_sgconfig_check(config_dir, global_config),
        _agent_files_check(state),
        _registry_check(config_dir, state_dir, global_config=global_config, current_repo=current_repo),
    ]
    optional = (_disabled_paths_check(state), _legacy_state_check(config_dir, state_dir))
    checks.extend(check for check in optional if check is not None)
    if not quick:
        checks.append(_global_rules_check(config_dir, global_config))
        checks.append(_global_command_rules_check(config_dir, global_config))
    return checks


def _repo_checks(
    repo_root: Path,
    config_dir: Path,
    *,
    global_config: GlobalConfig,
    quick: bool,
) -> list[Check]:
    copy_check = _worktree_copy_check(repo_root)
    if copy_check is not None:
        return [copy_check]
    if not repo_config_path(repo_root).is_file():
        return [
            Check(
                id="repo",
                ok=True,
                message="not a byor repo; run `byor init` to add project rules",
            )
        ]
    repo_config, repo_check = _repo_config_check(repo_root)
    checks = [
        repo_check,
        _sgconfig_check(repo_root, repo_config.paths),
        _rule_dirs_check(repo_root, repo_config.paths),
        _rule_visibility_check(repo_root, repo_config.paths),
        _ignore_block_check(repo_root),
    ]
    if not quick and repo_check.ok:
        checks.extend(_rule_checks(repo_root, repo_config.paths, config_dir=config_dir, global_config=global_config))
        checks.append(_command_rules_check(repo_root, repo_config.paths, global_config=global_config))
        gate_check = _gate_check(repo_root, repo_config)
        if gate_check is not None:
            checks.append(gate_check)
        gate_hook_check = _gate_hook_check(repo_root, repo_config)
        if gate_hook_check is not None:
            checks.append(gate_hook_check)
        vendored_check = _vendored_scripts_check(repo_root, repo_config)
        if vendored_check is not None:
            checks.append(vendored_check)
        shims_check = _git_shims_check(repo_root)
        if shims_check is not None:
            checks.append(shims_check)
    extra = _extra_checks_check(repo_root, repo_config, global_config=global_config, config_dir=config_dir)
    if extra is not None:
        checks.append(extra)
    return checks


def quick_doctor_problems(repo_root: Path, config_dir: Path) -> list[str]:
    return [
        f"doctor: {check.id}: {check.message}"
        for check in collect_checks(repo_root, config_dir, quick=True)
        if not check.ok
    ]


def render_checks(checks: list[Check]) -> list[str]:
    width = max(len(check.id) for check in checks)
    return [f"{'ok' if check.ok else 'FAIL':<4}  {check.id:<{width}}  {check.message}" for check in checks]


def _ast_grep_check(global_config: GlobalConfig) -> Check:
    try:
        version = ast_grep_version(resolve_ast_grep(global_config.ast_grep_command))
    except AstGrepNotFoundError as error:
        return Check(id="ast_grep_found", ok=False, message=str(error))
    return Check(id="ast_grep_found", ok=True, message=f"ast-grep {version}")


def _home_sgconfig_check(config_dir: Path, global_config: GlobalConfig) -> Check:
    path = home_sgconfig_path()
    if problem := dangling_link_problem(global_rules_dir(config_dir, global_config)):
        return Check(id="home_sgconfig", ok=False, message=f"no global rule applies: {problem}")
    if not path.is_file():
        return Check(
            id="home_sgconfig",
            ok=True,
            message="global ast-grep config not set up; run `byor install`",
        )
    try:
        data = load_yaml_mapping(path)
    except ConfigError as error:
        return Check(id="home_sgconfig", ok=False, message=str(error))
    rules_dir = global_rules_dir(config_dir, global_config)
    expected = Path(os.path.relpath(rules_dir, Path.home())).as_posix()
    rule_dirs = data.get("ruleDirs")
    if not isinstance(rule_dirs, list):
        # `byor install` cannot repair a malformed ruleDirs — it refuses to
        # rewrite the user's file — so the only real remedy is a hand edit.
        return Check(
            id="home_sgconfig",
            ok=False,
            message=f"~/sgconfig.yml has a non-list ruleDirs; edit it by hand so ruleDirs lists {expected}",
        )
    if expected not in rule_dirs:
        return Check(
            id="home_sgconfig",
            ok=False,
            message=f"~/sgconfig.yml does not list {expected}; run `byor install`",
        )
    return Check(
        id="home_sgconfig",
        ok=True,
        message="~/sgconfig.yml applies your global rules",
    )


def _global_rules_check(config_dir: Path, global_config: GlobalConfig) -> Check:
    rules_dir = global_rules_dir(config_dir, global_config)
    try:
        rules = load_rules(rules_dir)
    except (RuleValidationError, ConfigError) as error:
        return Check(id="global_rules", ok=False, message=str(error))
    duplicates = sorted(rule_id for rule_id, count in Counter(rule.id for rule in rules).items() if count > 1)
    if duplicates:
        return Check(
            id="global_rules",
            ok=False,
            message=f"duplicate global rule IDs: {', '.join(duplicates)}",
        )
    home_sgconfig = home_sgconfig_path()
    if home_sgconfig.is_file() and (cause := _ast_grep_load_error(home_sgconfig, global_config)):
        return Check(
            id="global_rules",
            ok=False,
            message=(
                f"ast-grep cannot load the rules ~/{home_sgconfig.name} lists, so the post-edit hook"
                f" checks nothing in repos without `byor init`: {cause}"
            ),
        )
    # ast-grep does not load a symlinked file inside a ruleDirs folder, so these
    # rules never fire through ~/sgconfig.yml in a repo without byor init. byor's
    # own loader follows the links, which is why they still parse here.
    linked = sorted(rule.path.name for rule in rules if rule.path.is_symlink())
    if linked:
        return Check(
            id="global_rules",
            ok=False,
            message=(
                f"global rule files are symlinks, which ast-grep skips in repos without byor init: "
                f"{', '.join(linked)}; link the whole rules folder or copy the files instead"
            ),
        )
    noun = "rule" if len(rules) == 1 else "rules"
    return Check(
        id="global_rules",
        ok=True,
        message=f"{len(rules)} global {noun} parse",
    )


# The hook fails open when ast-grep cannot load a rule, so only ast-grep itself can tell doctor
# that a scan would silently check nothing. A missing ast-grep is ast_grep_found's to report.
def _ast_grep_load_error(config: Path, global_config: GlobalConfig) -> str | None:
    try:
        executable = resolve_ast_grep(global_config.ast_grep_command)
    except AstGrepNotFoundError:
        return None
    return config_load_error(executable, config)


def _global_command_rules_check(config_dir: Path, global_config: GlobalConfig) -> Check:
    commands_dir = global_commands_dir(config_dir, global_config)
    try:
        rules = load_rules(commands_dir)
    except (RuleValidationError, ConfigError) as error:
        return Check(id="global_command_rules", ok=False, message=str(error))
    duplicates = sorted(rule_id for rule_id, count in Counter(rule.id for rule in rules).items() if count > 1)
    if duplicates:
        return Check(
            id="global_command_rules",
            ok=False,
            message=f"duplicate global command rule IDs: {', '.join(duplicates)}",
        )
    noun = "rule" if len(rules) == 1 else "rules"
    return Check(
        id="global_command_rules",
        ok=True,
        message=f"{len(rules)} global command {noun} parse",
    )


def _command_rules_check(repo_root: Path, paths: RepoPaths, *, global_config: GlobalConfig) -> Check:
    """Report whether the repo's command rules can actually gate commands.

    The hook path fails open, so a broken command rule silently disables the
    whole pre-command gate; this is the check that makes that visible. A
    non-Bash rule is a FAIL too — the gate parses commands as Bash and skips
    other languages, so such a rule can never fire.
    """
    try:
        rules = [rule for relpath in command_rule_dir_relpaths(paths) for rule in load_rules(repo_root / relpath)]
    except (RuleValidationError, ConfigError) as error:
        return Check(id="command_rules", ok=False, message=str(error))
    foreign = sorted(rule.id for rule in rules if rule.language.lower() != "bash")
    if foreign:
        return Check(
            id="command_rules",
            ok=False,
            message=f"command rules must use `language: Bash` to gate commands: {', '.join(foreign)}",
        )
    if rules:
        try:
            executable = resolve_ast_grep(global_config.ast_grep_command)
            scan_command(executable, "true", rules=[rule.content for rule in rules])
        except ByorError as error:
            first_line = str(error).splitlines()[0]
            return Check(
                id="command_rules",
                ok=False,
                message=f"command rules fail to load, disabling the pre-command gate: {first_line}",
            )
    subject = "1 command rule gates" if len(rules) == 1 else f"{len(rules)} command rules gate"
    return Check(
        id="command_rules",
        ok=True,
        message=f"{subject} shell commands",
    )


# Doctor never copies, so a fresh worktree gets the one command that does instead of `byor init`,
# which would write a fresh local.yml and lose the main checkout's settings for good.
def _worktree_copy_check(repo_root: Path) -> Check | None:
    try:
        pending = pending_copy(repo_root)
    except ConfigError:
        return None
    if pending is None:
        return None
    entry = worktree_disabled_entry(repo_root, load_machine_state(global_state_dir()))
    if entry is not None:
        # byor copies nothing into a disabled checkout's worktree, so `byor sync` would never clear this.
        return Check(
            id="repo",
            ok=True,
            message=f"byor is disabled here by {_home_display(entry)}, so it copies nothing into this worktree",
        )
    return Check(
        id="repo",
        ok=False,
        message=(
            f"this worktree has not copied byor's files from its main checkout "
            f"{_home_display(pending.main_root)} yet; run `byor sync`"
        ),
    )


def _repo_config_check(repo_root: Path) -> tuple[RepoConfig, Check]:
    try:
        config = load_repo_config(repo_root)
    except (RepoNotInitializedError, ConfigError) as error:
        return RepoConfig(), Check(id="repo_config", ok=False, message=str(error))
    return config, Check(
        id="repo_config",
        ok=True,
        message=".byor/config.yml is valid",
    )


def _sgconfig_check(repo_root: Path, paths: RepoPaths) -> Check:
    sgconfig = repo_root / paths.sgconfig
    if not sgconfig.is_file():
        return Check(
            id="sgconfig",
            ok=False,
            message=f"{paths.sgconfig} is missing; run `byor init`",
        )
    try:
        data = load_yaml_mapping(sgconfig)
    except ConfigError as error:
        return Check(id="sgconfig", ok=False, message=str(error))
    rule_dirs = data.get("ruleDirs")
    if not isinstance(rule_dirs, list):
        return Check(
            id="sgconfig",
            ok=False,
            message=f"{paths.sgconfig}: expected ruleDirs to be a list",
        )
    missing = [d for d in rule_dir_relpaths(paths) if d not in rule_dirs]
    if missing:
        return Check(
            id="sgconfig",
            ok=False,
            message=f"{paths.sgconfig} ruleDirs is missing: {', '.join(missing)}",
        )
    return Check(
        id="sgconfig",
        ok=True,
        message=f"{paths.sgconfig} lists all BYOR rule dirs",
    )


def _rule_dirs_check(repo_root: Path, paths: RepoPaths) -> Check:
    missing = [d for d in rule_dir_relpaths(paths) if not (repo_root / d).is_dir()]
    if missing:
        return Check(
            id="rule_dirs",
            ok=False,
            message=f"missing rule directories: {', '.join(missing)}; run `byor init`",
        )
    return Check(id="rule_dirs", ok=True, message="all rule directories exist")


def _rule_visibility_check(repo_root: Path, paths: RepoPaths) -> Check:
    personal_dirs = (
        paths.personal_local_rules,
        paths.personal_global_rules,
        paths.personal_packages_rules,
    )
    broken = [d for d in personal_dirs if not rule_visibility_ok(repo_root / d)]
    if broken:
        return Check(
            id="rules_visible",
            ok=False,
            message=(
                f"{', '.join(broken)} lacks the .ignore negations ast-grep needs"
                " to load git-ignored rules; run `byor init`"
            ),
        )
    return Check(
        id="rules_visible",
        ok=True,
        message="personal rule directories are visible to ast-grep",
    )


def _rule_checks(repo_root: Path, paths: RepoPaths, *, config_dir: Path, global_config: GlobalConfig) -> list[Check]:
    try:
        project = load_rules(repo_root / paths.project_rules)
        local = load_rules(repo_root / paths.personal_local_rules)
        canonical = load_canonical_rules(config_dir)
    except (RuleValidationError, ConfigError) as error:
        return [Check(id="rules_valid", ok=False, message=str(error))]
    checks = [
        Check(
            id="rules_valid",
            ok=True,
            message="all rule files load in ast-grep",
        )
    ]
    try:
        load_local_config(repo_root)
    except ConfigError as error:
        checks.append(Check(id="local_config", ok=False, message=f"{error}; fix .byor/local.yml by hand"))
        return checks
    try:
        check_id_conflicts(project, local, canonical_global=canonical.rules)
    except DuplicateRuleIdError as error:
        checks.append(Check(id="rule_ids_unique", ok=False, message=str(error)))
        return checks
    # After byor's own ID check, whose message names the scopes, ast-grep itself says whether the
    # rules the repo's sgconfig lists load at all, including package copies and fields it rejects.
    sgconfig = repo_root / paths.sgconfig
    if sgconfig.is_file() and (cause := _ast_grep_load_error(sgconfig, global_config)):
        checks[0] = Check(
            id="rules_valid",
            ok=False,
            message=(
                f"ast-grep cannot load the rules {paths.sgconfig} lists, so the post-edit hook"
                f" checks nothing in this repo: {cause}"
            ),
        )
        return checks
    checks.append(
        Check(
            id="rule_ids_unique",
            ok=True,
            message="effective rule IDs are unique",
        )
    )
    try:
        plans = repo_plans(repo_root, canonical)
    except (DuplicateRuleIdError, RuleValidationError, ConfigError) as error:
        checks.append(Check(id="package_rules", ok=False, message=str(error)))
        return checks
    global_stale = mirror_contents(repo_root / paths.personal_global_rules) != plans.rules.global_plan.desired
    packages_stale = mirror_contents(plans.rules.packages_dir) != plans.rules.packages_plan.desired
    if global_stale or packages_stale or plans.commands.is_stale():
        message = "rule copies are stale; run `byor sync`"
        checks.append(Check(id="sync_fresh", ok=False, message=message))
    else:
        checks.append(Check(id="sync_fresh", ok=True, message="rule copies are in sync"))
    return checks


# The personal state an ignore block hides. Tracked .gitkeep and .ignore files in these
# directories are meant to be committed, so only the rule copies and local.yml count.
PERSONAL_PATHS = (".byor/local.yml", ".byor/rules/personal", ".byor/commands/personal")
SHOWN_TRACKED_FILES = 3


def _tracked_personal_files(repo_root: Path) -> list[str]:
    output = git_stdout(repo_root, "ls-files", "-z", "--", *PERSONAL_PATHS) or ""
    return [name for name in output.split("\0") if name.endswith((".yml", ".yaml"))]


def _ignore_block_check(repo_root: Path) -> Check:
    statuses = ignore_block_statuses(repo_root)

    def having(state: str) -> list[IgnoreBlockStatus]:
        return [status for status in statuses if status.state == state]

    def listed(selected: list[IgnoreBlockStatus]) -> str:
        return ", ".join(Path(os.path.relpath(status.path, repo_root)).as_posix() for status in selected)

    outdated = having("outdated")
    stuck = [status for status in outdated if not status.writable]
    covered = bool(having("current"))
    tracked = _tracked_personal_files(repo_root) if covered else []
    more = len(tracked) - SHOWN_TRACKED_FILES
    shown = ", ".join(tracked[:SHOWN_TRACKED_FILES]) + (f" and {more} more" if more > 0 else "")
    reasons = "; ".join(f"{listed([status])}: {status.reason}" for status in having("unreadable"))
    # Checked in order, and the first case that applies decides. An outdated block counts even
    # beside a current one, so doctor agrees with `byor sync --check`. A `None` message means ok.
    cases: list[tuple[bool, str | None]] = [
        (
            bool(stuck),
            f"the byor ignore block in {listed(stuck)} is out of date and the file is not writable;"
            " make it writable, then run `byor sync`",
        ),
        (
            bool(outdated),
            f"the byor ignore block in {listed(outdated)} is out of date"
            f"{'' if covered else ', so some personal byor files are committable'}; run `byor sync` to update it",
        ),
        (
            bool(tracked),
            f"git already tracks personal byor files, which no ignore block can hide: {shown};"
            " untrack them with `git rm --cached` and commit",
        ),
        (covered, None),
        (
            # `byor init` would replace everything from the first begin marker on, so it is not the fix.
            bool(having("damaged")),
            f"the byor ignore block in {listed(having('damaged'))} is damaged: it needs exactly one begin marker"
            " and one end marker after it; fix the markers by hand, and byor then keeps the block current",
        ),
        (
            bool(having("edited")),
            f"the byor ignore block in {listed(having('edited'))} was edited, and its lines no longer say whether"
            " it is for a shared or a private setup, so byor leaves it alone; restore byor's lines between the markers",
        ),
        (bool(reasons), f"byor cannot read an ignore file, so it cannot check the byor block there ({reasons})"),
        (
            True,
            "the byor ignore block is gone, so personal rules and .byor/local.yml are committable;"
            " run `byor init` to restore it",
        ),
    ]
    message = next(message for applies, message in cases if applies)
    if message is None:
        return Check(id="ignore_block", ok=True, message="personal rules and .byor/local.yml are git-ignored")
    return Check(id="ignore_block", ok=False, message=message)


def _git_shims_check(repo_root: Path) -> Check | None:
    findings = shim_findings(repo_root)
    if findings is None:
        return None
    if findings.problems:
        return Check(id="git_shims", ok=False, message="; ".join(findings.problems))
    if findings.notes:
        return Check(id="git_shims", ok=True, message="; ".join(findings.notes))
    return Check(id="git_shims", ok=True, message="git hook shims are installed and current")


def _gate_check(repo_root: Path, repo_config: RepoConfig) -> Check | None:
    if not repo_config.gate:
        return None
    stale = stale_gate_files(repo_root, repo_config)
    if stale:
        return Check(
            id="gate_files",
            ok=False,
            message=f"gate files are stale: {', '.join(stale)}; run `byor sync`",
        )
    gaps = user_owned_gate_gaps(repo_root, repo_config)
    if gaps:
        return Check(
            id="gate_files",
            ok=False,
            message=f"{'; '.join(gaps)}; run `byor init --gate` to print the lines to add",
        )
    return Check(id="gate_files", ok=True, message="gate files match the configured checks")


def _gate_hook_check(repo_root: Path, repo_config: RepoConfig) -> Check | None:
    """Add an informational row when a gate repo never ran `pre-commit install`.

    The CI leg still enforces, so a missing local hook is ok-severity, in the
    style of the user-owned-hook note. Any pre-commit hook file present is
    assumed active; its internals are never verified.
    """
    if not repo_config.gate or not (repo_root / CONFIG_RELPATH).is_file():
        return None
    if precommit_hook_installed(repo_root) is not False:
        return None
    return Check(
        id="gate_hook",
        ok=True,
        message="local pre-commit gate not installed; run `uvx pre-commit install` (CI still enforces)",
    )


def _vendored_scripts_check(repo_root: Path, repo_config: RepoConfig) -> Check | None:
    direct = referenced_vendored_scripts(repo_config.checks)
    if not direct:
        return None
    relpaths = transitive_vendored_scripts(repo_root, direct)
    invoked = directly_invoked_vendored_scripts(repo_config.checks)
    transitive_only = set(relpaths) - set(direct)
    problems = [
        problem
        for relpath in relpaths
        for problem in vendored_script_problems(
            repo_root,
            relpath,
            executable_required=relpath in invoked or relpath in transitive_only,
        )
    ]
    if problems:
        return Check(id="vendored_scripts", ok=False, message="; ".join(problems))
    noun = "script" if len(relpaths) == 1 else "scripts"
    return Check(
        id="vendored_scripts",
        ok=True,
        message=f"{len(relpaths)} vendored check {noun} present and current",
    )


def _registry_check(config_dir: Path, state_dir: Path, *, global_config: GlobalConfig, current_repo: Path) -> Check:
    repos = load_repo_registry(repo_registry_path(state_dir, global_config))
    # The repo doctor runs in may be unregistered, as every worktree is, so check its packages too.
    registered = {repo.resolve() for repo in repos}
    is_unregistered = local_config_path(current_repo).is_file() and current_repo.resolve() not in registered
    unregistered = [current_repo] if is_unregistered else []
    missing = [f"{repo} no longer exists" for repo in repos if not repo.is_dir()]
    # Keyed on the directory itself, so `~/clients/c1` and `~/CLIENTS/C1` on a case-insensitive
    # volume count as one repo registered twice.
    entries: dict[object, list[Path]] = {}
    for repo in repos:
        entries.setdefault(_directory_identity(repo), []).append(repo.resolve())
    duplicates = sorted(spellings[0] for spellings in entries.values() if len(spellings) > 1)
    problems = missing + [f"duplicate registry entries for {repo}" for repo in duplicates]
    if missing:
        problems.append("run `byor sync --all --prune`")
    available = set(available_packages(config_dir, global_config))
    problems.extend(_missing_package_problems([*repos, *unregistered], available))
    if problems:
        return Check(
            id="registered_repos",
            ok=False,
            message="; ".join(problems),
        )
    return Check(
        id="registered_repos",
        ok=True,
        message="all registered repository paths exist and their packages are installed",
    )


def _directory_identity(repo: Path) -> object:
    try:
        stat = repo.stat()
    except OSError:
        return repo.resolve()
    return (stat.st_dev, stat.st_ino)


# sync loads a missing package as zero rules, so without this a repo silently
# loses the package's rules and checks after the package is deleted or renamed.
def _missing_package_problems(repos: list[Path], available: set[str]) -> list[str]:
    problems: list[str] = []
    missing: list[str] = []
    for repo in repos:
        if not repo.is_dir():
            continue
        try:
            local = load_local_config(repo)
        except ConfigError as error:
            problems.append(str(error))
            continue
        missing.extend(f"{_home_display(repo)} ({name})" for name in local.packages if name not in available)
    if missing:
        problems.append(
            f"repos name packages that are not installed: {', '.join(missing)}; "
            "run `byor package remove NAME` in each repo, or restore the package"
        )
    return problems


def _disabled_paths_check(state: MachineState) -> Check | None:
    if not state.disabled_repos:
        return None
    listed = ", ".join(_home_display(entry) for entry in state.disabled_repos)
    count = len(state.disabled_repos)
    noun = "path" if count == 1 else "paths"
    return Check(id="disabled_paths", ok=True, message=f"{count} {noun} disabled: {listed}")


def _legacy_state_check(config_dir: Path, state_dir: Path) -> Check | None:
    try:
        leftovers = legacy_machine_state(config_dir, state_dir)
    except ConfigError as error:
        return Check(id="legacy_state", ok=False, message=f"{error}; fix config.yml by hand")
    if not leftovers:
        return None
    listed = ", ".join(leftovers)
    return Check(
        id="legacy_state",
        ok=False,
        message=(
            f"byor no longer reads these entries in {_home_display(config_dir)}, because it keeps them in "
            f"{_home_display(state_dir)} now: {listed}; delete them"
        ),
    )


def _home_display(path: Path) -> str:
    try:
        return f"~/{path.relative_to(Path.home()).as_posix()}"
    except ValueError:
        return str(path)


def _extra_checks_check(
    repo_root: Path,
    repo_config: RepoConfig,
    *,
    global_config: GlobalConfig,
    config_dir: Path,
) -> Check | None:
    try:
        local_config = load_local_config(repo_root)
    except ConfigError as error:
        return Check(id="extra_checks", ok=False, message=f"{error}; fix .byor/local.yml by hand")
    try:
        package_checks = [
            (f"package:{name}", check)
            for name in local_config.packages
            for check in load_package_checks(config_dir, global_config, name=name)
        ]
    except ConfigError as error:
        return Check(id="extra_checks", ok=False, message=str(error))
    if not repo_config.checks and not global_config.checks and not package_checks:
        return None
    effective = effective_checks(repo_config, global_config, local_config=local_config, package_checks=package_checks)
    if not effective:
        return Check(
            id="extra_checks",
            ok=True,
            message="all configured checks are excluded",
        )
    listed = ", ".join(f"{check.name} ({check.origin})" for check in effective)
    return Check(id="extra_checks", ok=True, message=f"checks: {listed}")


def _agent_files_check(state: MachineState) -> Check:
    supported = [agent for agent in state.agents if agent in AGENT_CHOICES]
    unsupported = [agent for agent in state.agents if agent not in AGENT_CHOICES]
    problems = _recorded_agent_problems(supported) + _unrecorded_agent_problems(supported)
    if unsupported:
        problems.append(
            f"state.yml lists agents byor does not support: {', '.join(unsupported)}; the next byor command drops them"
        )
    if problems:
        return Check(id="agent_files", ok=False, message="; ".join(problems))
    if not supported:
        return Check(id="agent_files", ok=True, message="no AI agents configured")
    agents = ", ".join(supported)
    return Check(
        id="agent_files",
        ok=True,
        message=f"agent integrations installed for: {agents}",
    )


def _recorded_agent_problems(agents: list[str]) -> list[str]:
    problems: list[str] = []
    for agent in agents:
        # A malformed harness config is itself a finding: report it per agent so
        # one broken file cannot crash doctor or hide the other agents' health.
        try:
            agent_problems = agent_file_problems([agent])
        except ConfigError as error:
            problems.append(f"{error}; fix the JSON by hand")
            continue
        problems.extend(f"{problem}; run `byor install`" for problem in agent_problems)
    return problems


def _unrecorded_agent_problems(recorded: list[str]) -> list[str]:
    scan = unrecorded_agents(recorded)
    problems = [f"{problem}; fix the file by hand" for problem in scan.problems]
    if scan.found:
        listed = ", ".join(scan.found)
        problems.append(
            f"byor integrations are installed for agents missing from `agents` in byor's state.yml: {listed}; "
            "any self-healing command such as `byor list` records them, "
            "or run `byor hook install --agent NAME` for each"
        )
    return problems
