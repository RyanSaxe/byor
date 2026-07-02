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

from byor.agents.install import agent_file_problems
from byor.commands.gate import (
    referenced_vendored_scripts,
    stale_gate_files,
    vendored_script_problems,
)
from byor.config import (
    GlobalConfig,
    RepoConfig,
    RepoPaths,
    global_rules_dir,
    load_global_config,
    load_local_config,
    load_repo_config,
    load_repo_registry,
    repo_config_path,
    repo_registry_path,
    rule_dir_relpaths,
)
from byor.errors import (
    AstGrepNotFoundError,
    ConfigError,
    DuplicateRuleIdError,
    RepoNotInitializedError,
    RuleValidationError,
)
from byor.io.output import write_line, write_lines
from byor.io.paths import global_config_dir, home_sgconfig_path, resolve_repo_root
from byor.io.yamlio import load_yaml_mapping
from byor.rules.rules import load_rules
from byor.rules.sync import (
    compute_sync_plan,
    load_canonical_rules,
    mirror_contents,
    repo_plans,
)
from byor.scaffold.githooks import shim_problems
from byor.scaffold.ignore import ignore_block_current, rule_visibility_ok
from byor.scan.astgrep import ast_grep_version, resolve_ast_grep
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
    checks = _global_checks(config_dir, global_config, quick=quick)
    checks.extend(
        _repo_checks(
            repo_root,
            config_dir,
            global_config=global_config,
            quick=quick,
        )
    )
    return checks


def _global_checks(config_dir: Path, global_config: GlobalConfig, *, quick: bool) -> list[Check]:
    checks = [
        _ast_grep_check(global_config),
        _home_sgconfig_check(config_dir, global_config),
        _agent_files_check(global_config),
        _registry_check(config_dir, global_config),
    ]
    if not quick:
        checks.append(_global_rules_check(config_dir, global_config))
    return checks


def _repo_checks(
    repo_root: Path,
    config_dir: Path,
    *,
    global_config: GlobalConfig,
    quick: bool,
) -> list[Check]:
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
        checks.extend(_rule_checks(repo_root, repo_config.paths, config_dir=config_dir))
        gate_check = _gate_check(repo_root, repo_config)
        if gate_check is not None:
            checks.append(gate_check)
        vendored_check = _vendored_scripts_check(repo_root, repo_config)
        if vendored_check is not None:
            checks.append(vendored_check)
        shims_check = _git_shims_check(repo_root)
        if shims_check is not None:
            checks.append(shims_check)
    extra = _extra_checks_check(repo_root, repo_config, global_config=global_config)
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
    if not isinstance(rule_dirs, list) or expected not in rule_dirs:
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
    noun = "rule" if len(rules) == 1 else "rules"
    return Check(
        id="global_rules",
        ok=True,
        message=f"{len(rules)} global {noun} parse",
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


def _rule_checks(repo_root: Path, paths: RepoPaths, *, config_dir: Path) -> list[Check]:
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
            message="all rule files parse with required fields",
        )
    ]
    try:
        local_config = load_local_config(repo_root)
    except ConfigError as error:
        checks.append(Check(id="local_config", ok=False, message=f"{error}; fix .byor/local.yml by hand"))
        return checks
    try:
        plan = compute_sync_plan(
            project,
            local,
            excluded_rule_ids=local_config.excluded_rule_ids,
            excluded_tags=local_config.excluded_rule_tags,
            canonical=canonical,
        )
    except DuplicateRuleIdError as error:
        checks.append(Check(id="rule_ids_unique", ok=False, message=str(error)))
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
    global_stale = mirror_contents(repo_root / paths.personal_global_rules) != plan.desired
    packages_stale = mirror_contents(plans.packages_dir) != plans.packages_plan.desired
    if global_stale or packages_stale:
        message = "rule copies are stale; run `byor sync`"
        checks.append(Check(id="sync_fresh", ok=False, message=message))
    else:
        checks.append(Check(id="sync_fresh", ok=True, message="rule copies are in sync"))
    return checks


def _ignore_block_check(repo_root: Path) -> Check:
    if ignore_block_current(repo_root):
        return Check(
            id="ignore_block",
            ok=True,
            message="personal rules and .byor/local.yml are git-ignored",
        )
    return Check(
        id="ignore_block",
        ok=False,
        message=(
            "the byor ignore block is gone, so personal rules and .byor/local.yml"
            " are committable; run `byor init` to restore it"
        ),
    )


def _git_shims_check(repo_root: Path) -> Check | None:
    problems = shim_problems(repo_root)
    if problems is None:
        return None
    if problems:
        return Check(id="git_shims", ok=False, message="; ".join(problems))
    return Check(id="git_shims", ok=True, message="git hook shims are installed and current")


def _gate_check(repo_root: Path, repo_config: RepoConfig) -> Check | None:
    if not repo_config.gate:
        return None
    stale = stale_gate_files(repo_root, repo_config)
    if stale:
        return Check(
            id="gate_files",
            ok=False,
            message=f"gate files are stale: {', '.join(stale)}; run `byor init --gate`",
        )
    return Check(id="gate_files", ok=True, message="gate files match the configured checks")


def _vendored_scripts_check(repo_root: Path, repo_config: RepoConfig) -> Check | None:
    relpaths = referenced_vendored_scripts(repo_config.checks)
    if not relpaths:
        return None
    problems = [problem for relpath in relpaths for problem in vendored_script_problems(repo_root, relpath)]
    if problems:
        return Check(id="vendored_scripts", ok=False, message="; ".join(problems))
    noun = "script" if len(relpaths) == 1 else "scripts"
    return Check(
        id="vendored_scripts",
        ok=True,
        message=f"{len(relpaths)} vendored check {noun} present and current",
    )


def _registry_check(config_dir: Path, global_config: GlobalConfig) -> Check:
    repos = load_repo_registry(repo_registry_path(config_dir, global_config))
    problems = [f"{repo} no longer exists" for repo in repos if not repo.is_dir()]
    tallies = Counter(repo.resolve() for repo in repos)
    problems.extend(f"duplicate registry entries for {repo}" for repo, count in sorted(tallies.items()) if count > 1)
    if problems:
        return Check(
            id="registered_repos",
            ok=False,
            message="; ".join(problems),
        )
    return Check(
        id="registered_repos",
        ok=True,
        message="all registered repository paths exist",
    )


def _extra_checks_check(
    repo_root: Path,
    repo_config: RepoConfig,
    *,
    global_config: GlobalConfig,
) -> Check | None:
    if not repo_config.checks and not global_config.checks:
        return None
    try:
        local_config = load_local_config(repo_root)
    except ConfigError as error:
        return Check(id="extra_checks", ok=False, message=f"{error}; fix .byor/local.yml by hand")
    effective = effective_checks(repo_config, global_config, local_config=local_config)
    if not effective:
        return Check(
            id="extra_checks",
            ok=True,
            message="all configured checks are excluded",
        )
    listed = ", ".join(f"{check.name} ({check.origin})" for check in effective)
    return Check(id="extra_checks", ok=True, message=f"checks: {listed}")


def _agent_files_check(global_config: GlobalConfig) -> Check:
    if not global_config.agents:
        return Check(id="agent_files", ok=True, message="no AI agents configured")
    problems: list[str] = []
    for agent in global_config.agents:
        # A malformed harness config is itself a finding: report it per agent so
        # one broken file cannot crash doctor or hide the other agents' health.
        try:
            agent_problems = agent_file_problems([agent])
        except ConfigError as error:
            problems.append(f"{error}; fix the JSON by hand")
            continue
        problems.extend(f"{problem}; run `byor install`" for problem in agent_problems)
    if problems:
        return Check(id="agent_files", ok=False, message="; ".join(problems))
    agents = ", ".join(global_config.agents)
    return Check(
        id="agent_files",
        ok=True,
        message=f"agent integrations installed for: {agents}",
    )
