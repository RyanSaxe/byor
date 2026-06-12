"""`byolsp doctor`: actionable installation health checks (SPEC 15.3)."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

from byolsp.agents import AGENT_INSTRUCTIONS_RELPATH
from byolsp.astgrep import ast_grep_version, resolve_ast_grep
from byolsp.config import (
    GlobalConfig,
    RepoConfig,
    RepoPaths,
    load_global_config,
    load_local_config,
    load_repo_config,
    load_repo_registry,
    repo_registry_path,
    rule_dir_relpaths,
)
from byolsp.errors import (
    AstGrepNotFound,
    ConfigError,
    DuplicateRuleId,
    RepoNotInitialized,
    RuleValidationError,
)
from byolsp.paths import global_config_dir, resolve_repo_root
from byolsp.rules import load_rules
from byolsp.sync import compute_sync_plan, load_canonical_rules, mirror_contents
from byolsp.yamlio import load_yaml_mapping


@dataclass
class Check:
    """One doctor check, matching the SPEC 15.3 JSON shape."""

    id: str
    ok: bool
    message: str


def run_doctor(args: argparse.Namespace) -> int:
    repo_root = resolve_repo_root(explicit=args.repo)
    checks = collect_checks(repo_root, global_config_dir(), quick=args.quick)
    ok = all(check.ok for check in checks)
    if args.json:
        payload = {"ok": ok, "checks": [asdict(check) for check in checks]}
        print(json.dumps(payload, indent=2))
    else:
        for line in render_checks(checks):
            print(line)
    return 0 if ok else 1


def collect_checks(repo_root: Path, config_dir: Path, quick: bool) -> list[Check]:
    """Run the SPEC 15.3 checks. `quick` skips recursive rule validation
    (rule parsing, ID uniqueness, sync staleness) but keeps the cheap checks.
    """
    global_config = load_global_config(config_dir)
    repo_config, repo_check = _repo_config_check(repo_root)
    checks = [_ast_grep_check(global_config), repo_check]
    checks.append(_sgconfig_check(repo_root, repo_config.paths))
    checks.append(_rule_dirs_check(repo_root, repo_config.paths))
    if not quick and repo_check.ok:
        checks.extend(_rule_checks(repo_root, repo_config.paths, config_dir))
    checks.append(_registry_check(config_dir, global_config))
    checks.append(_agent_files_check(repo_root, repo_config))
    return checks


def quick_doctor_problems(repo_root: Path, config_dir: Path) -> list[str]:
    """Failing `doctor --quick` checks as printable lines, for the post-action
    step that init, add, edit, and promote share (SPEC 15.1, 15.4-15.6).
    """
    return [
        f"doctor: {check.id}: {check.message}"
        for check in collect_checks(repo_root, config_dir, quick=True)
        if not check.ok
    ]


def render_checks(checks: list[Check]) -> list[str]:
    width = max(len(check.id) for check in checks)
    return [
        f"{'ok' if check.ok else 'FAIL':<4}  {check.id:<{width}}  {check.message}"
        for check in checks
    ]


def _ast_grep_check(global_config: GlobalConfig) -> Check:
    try:
        version = ast_grep_version(resolve_ast_grep(global_config.ast_grep_command))
    except AstGrepNotFound as error:
        return Check("ast_grep_found", False, str(error))
    return Check("ast_grep_found", True, f"ast-grep {version}")


def _repo_config_check(repo_root: Path) -> tuple[RepoConfig, Check]:
    """Load the repo config; later checks fall back to defaults when it fails."""
    try:
        config = load_repo_config(repo_root)
    except (RepoNotInitialized, ConfigError) as error:
        return RepoConfig(), Check("repo_config", False, str(error))
    return config, Check("repo_config", True, ".byolsp/config.yml is valid")


def _sgconfig_check(repo_root: Path, paths: RepoPaths) -> Check:
    sgconfig = repo_root / paths.sgconfig
    if not sgconfig.is_file():
        return Check(
            "sgconfig", False, f"{paths.sgconfig} is missing; run `byolsp init`"
        )
    try:
        data = load_yaml_mapping(sgconfig)
    except ConfigError as error:
        return Check("sgconfig", False, str(error))
    rule_dirs = data.get("ruleDirs")
    if not isinstance(rule_dirs, list):
        return Check(
            "sgconfig", False, f"{paths.sgconfig}: expected ruleDirs to be a list"
        )
    missing = [d for d in rule_dir_relpaths(paths) if d not in rule_dirs]
    if missing:
        return Check(
            "sgconfig",
            False,
            f"{paths.sgconfig} ruleDirs is missing: {', '.join(missing)}",
        )
    return Check("sgconfig", True, f"{paths.sgconfig} lists all BYOLSP rule dirs")


def _rule_dirs_check(repo_root: Path, paths: RepoPaths) -> Check:
    missing = [d for d in rule_dir_relpaths(paths) if not (repo_root / d).is_dir()]
    if missing:
        return Check(
            "rule_dirs",
            False,
            f"missing rule directories: {', '.join(missing)}; run `byolsp init`",
        )
    return Check("rule_dirs", True, "all rule directories exist")


def _rule_checks(repo_root: Path, paths: RepoPaths, config_dir: Path) -> list[Check]:
    """Recursive rule validation: parsing, ID uniqueness, sync staleness."""
    try:
        project = load_rules(repo_root / paths.project_rules)
        local = load_rules(repo_root / paths.personal_local_rules)
        canonical = load_canonical_rules(config_dir)
    except (RuleValidationError, ConfigError) as error:
        return [Check("rules_valid", False, str(error))]
    checks = [Check("rules_valid", True, "all rule files parse with required fields")]
    try:
        plan = compute_sync_plan(
            project, local, load_local_config(repo_root).excluded_rule_ids, canonical
        )
    except DuplicateRuleId as error:
        checks.append(Check("rule_ids_unique", False, str(error)))
        return checks
    checks.append(Check("rule_ids_unique", True, "effective rule IDs are unique"))
    if mirror_contents(repo_root / paths.personal_global_rules) != plan.desired:
        message = "global rule copies are stale; run `byolsp sync`"
        checks.append(Check("sync_fresh", False, message))
    else:
        checks.append(Check("sync_fresh", True, "global rule copies are in sync"))
    return checks


def _registry_check(config_dir: Path, global_config: GlobalConfig) -> Check:
    repos = load_repo_registry(repo_registry_path(config_dir, global_config))
    problems = [f"{repo} no longer exists" for repo in repos if not repo.is_dir()]
    tallies = Counter(repo.resolve() for repo in repos)
    problems.extend(
        f"duplicate registry entries for {repo}"
        for repo, count in sorted(tallies.items())
        if count > 1
    )
    if problems:
        return Check("registered_repos", False, "; ".join(problems))
    return Check("registered_repos", True, "all registered repository paths exist")


def _agent_files_check(repo_root: Path, repo_config: RepoConfig) -> Check:
    """Configured agents need their instruction files (currently the shared README;
    per-agent adapters arrive with `byolsp hook install`, SPEC 15.10).
    """
    if not repo_config.agents:
        return Check("agent_files", True, "no AI agents configured")
    if (repo_root / AGENT_INSTRUCTIONS_RELPATH).is_file():
        agents = ", ".join(repo_config.agents)
        return Check("agent_files", True, f"agent instructions installed for: {agents}")
    return Check(
        "agent_files",
        False,
        f"{AGENT_INSTRUCTIONS_RELPATH} is missing; rerun `byolsp init`",
    )
