"""Mirror canonical global rules into repositories (SPEC sections 3, 13, 15.2)."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from byolsp.config import (
    RepoPaths,
    global_rules_dir,
    load_global_config,
    load_local_config,
    load_repo_config,
    load_repo_registry,
    repo_config_path,
    repo_registry_path,
)
from byolsp.fsio import write_text_atomic
from byolsp.paths import global_config_dir, resolve_repo_root
from byolsp.rules import check_id_conflicts, discover_rule_files, load_rules

STALE_EXIT_CODE = 3


@dataclass
class SyncPlan:
    """What the personal/global mirror should contain, and what was skipped."""

    desired: dict[str, str]
    """Relative path below the global rules root -> rule file content."""

    skipped: list[tuple[str, str]]
    """(rule ID, human-readable reason), in canonical discovery order."""


@dataclass
class MirrorResult:
    written: int
    removed: int

    @property
    def changed(self) -> bool:
        return self.written > 0 or self.removed > 0


@dataclass
class SyncOutcome:
    plan: SyncPlan
    result: MirrorResult


def compute_sync_plan(
    repo_root: Path,
    paths: RepoPaths,
    excluded_rule_ids: Iterable[str],
    global_rules_root: Path,
) -> SyncPlan:
    """SPEC 13 steps 1-5: discover and validate rules, decide the mirror contents.

    Step 7 (combined effective IDs are unique) holds by construction: project
    and local IDs are unique and disjoint per check_id_conflicts, and every
    kept global rule has an ID outside both sets and unique among global rules.
    """
    project = load_rules(repo_root / paths.project_rules)
    local = load_rules(repo_root / paths.personal_local_rules)
    canonical = load_rules(global_rules_root)
    check_id_conflicts(project, local, canonical)
    project_ids = {rule.id for rule in project}
    local_ids = {rule.id for rule in local}
    excluded = set(excluded_rule_ids)
    desired: dict[str, str] = {}
    skipped: list[tuple[str, str]] = []
    for rule in canonical:
        reason = _skip_reason(rule.id, project_ids, local_ids, excluded)
        if reason is None:
            relpath = rule.path.relative_to(global_rules_root).as_posix()
            desired[relpath] = rule.path.read_text(encoding="utf-8")
        else:
            skipped.append((rule.id, reason))
    return SyncPlan(desired=desired, skipped=skipped)


def mirror_contents(mirror_dir: Path) -> dict[str, str]:
    """The YAML files currently in the mirror, as relative path -> content."""
    return {
        path.relative_to(mirror_dir).as_posix(): path.read_text(encoding="utf-8")
        for path in discover_rule_files(mirror_dir)
    }


def is_stale(mirror_dir: Path, desired: dict[str, str]) -> bool:
    """The cheap staleness check (SPEC 13): path and content comparison."""
    return mirror_contents(mirror_dir) != desired


def mirror_global_rules(mirror_dir: Path, desired: dict[str, str]) -> MirrorResult:
    """Make the mirror's YAML contents exactly `desired` (SPEC 13 step 6).

    Copies new and changed files, deletes YAML files not in `desired`, prunes
    empty subdirectories, and leaves non-YAML files (.gitkeep) alone.
    """
    actual = mirror_contents(mirror_dir)
    written = 0
    for relpath, content in desired.items():
        if actual.get(relpath) != content:
            write_text_atomic(mirror_dir / relpath, content)
            written += 1
    removed = 0
    for relpath in actual:
        if relpath not in desired:
            (mirror_dir / relpath).unlink()
            removed += 1
    _prune_empty_dirs(mirror_dir)
    return MirrorResult(written=written, removed=removed)


def sync_repo(repo_root: Path, config_dir: Path) -> SyncOutcome:
    """Sync one repository: compute the plan and mirror it into place."""
    plan, mirror_dir = _repo_plan(repo_root, config_dir)
    return SyncOutcome(plan=plan, result=mirror_global_rules(mirror_dir, plan.desired))


def repo_is_stale(repo_root: Path, config_dir: Path) -> bool:
    plan, mirror_dir = _repo_plan(repo_root, config_dir)
    return is_stale(mirror_dir, plan.desired)


def heal_repo(repo_root: Path, config_dir: Path) -> str | None:
    """The self-heal preamble (SPEC 15): sync, returning one line when changed.

    Uninitialized repositories are skipped silently: the command being run
    will fail with its own clearer RepoNotInitialized error.
    """
    if not repo_config_path(repo_root).is_file():
        return None
    result = sync_repo(repo_root, config_dir).result
    if not result.changed:
        return None
    return f"byolsp: synced {summarize_changes(result)}"


def summarize_changes(result: MirrorResult) -> str:
    """E.g. '2 updated global rules' or '1 updated, 1 removed global rules'."""
    parts: list[str] = []
    if result.written:
        parts.append(f"{result.written} updated")
    if result.removed:
        parts.append(f"{result.removed} removed")
    total = result.written + result.removed
    return f"{', '.join(parts)} global rule{'' if total == 1 else 's'}"


def run_sync(args: argparse.Namespace) -> int:
    config_dir = global_config_dir()
    if args.all:
        return _sync_all(config_dir, check=args.check)
    repo_root = resolve_repo_root(explicit=args.repo)
    if args.check:
        return _report_staleness(repo_root, config_dir)
    _sync_and_report(repo_root, config_dir)
    return 0


def _sync_all(config_dir: Path, check: bool) -> int:
    """Sync (or check) every registered repository, warning on skipped entries."""
    registry_path = repo_registry_path(config_dir, load_global_config(config_dir))
    exit_code = 0
    for repo_root in load_repo_registry(registry_path):
        if not repo_root.is_dir():
            print(
                f"byolsp: skipping {repo_root}: path no longer exists",
                file=sys.stderr,
            )
            continue
        if not repo_config_path(repo_root).is_file():
            print(
                f"byolsp: skipping {repo_root}: no .byolsp/config.yml",
                file=sys.stderr,
            )
            continue
        if check:
            exit_code = max(exit_code, _report_staleness(repo_root, config_dir))
        else:
            _sync_and_report(repo_root, config_dir)
    return exit_code


def _sync_and_report(repo_root: Path, config_dir: Path) -> None:
    """Sync one repo and print the SPEC 15.2 output."""
    outcome = sync_repo(repo_root, config_dir)
    print(f"Synced {_count(len(outcome.plan.desired), 'global rule')} into {repo_root}")
    if outcome.plan.skipped:
        print(f"Skipped {_count(len(outcome.plan.skipped), 'global rule')}:")
        for rule_id, reason in outcome.plan.skipped:
            print(f"  {rule_id}: {reason}")


def _report_staleness(repo_root: Path, config_dir: Path) -> int:
    """`sync --check`: report without writing; exit 3 when stale (SPEC 15.2)."""
    if repo_is_stale(repo_root, config_dir):
        print(f"Sync is stale in {repo_root}; run `byolsp sync`.")
        return STALE_EXIT_CODE
    print(f"Sync is fresh in {repo_root}")
    return 0


def _repo_plan(repo_root: Path, config_dir: Path) -> tuple[SyncPlan, Path]:
    repo_config = load_repo_config(repo_root)
    local_config = load_local_config(repo_root)
    global_config = load_global_config(config_dir)
    plan = compute_sync_plan(
        repo_root,
        repo_config.paths,
        local_config.excluded_rule_ids,
        global_rules_dir(config_dir, global_config),
    )
    return plan, repo_root / repo_config.paths.personal_global_rules


def _skip_reason(
    rule_id: str,
    project_ids: set[str],
    local_ids: set[str],
    excluded: set[str],
) -> str | None:
    """Why a canonical global rule is not mirrored; overrides trump exclusion."""
    if rule_id in project_ids:
        return "overridden by project rule"
    if rule_id in local_ids:
        return "overridden by local rule"
    if rule_id in excluded:
        return "excluded in .byolsp/local.yml"
    return None


def _prune_empty_dirs(root: Path) -> None:
    """Remove empty subdirectories below root, deepest first; keep root itself."""
    if not root.is_dir():
        return
    subdirectories = sorted(
        (path for path in root.rglob("*") if path.is_dir()), reverse=True
    )
    for directory in subdirectories:
        if not any(directory.iterdir()):
            directory.rmdir()


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}{'' if number == 1 else 's'}"
