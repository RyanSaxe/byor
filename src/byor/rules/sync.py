"""Mirror canonical global rules into repositories."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from byor.config import (
    global_rules_dir,
    load_global_config,
    load_local_config,
    load_repo_config,
    load_repo_registry,
    repo_config_path,
    repo_registry_path,
)
from byor.io.fsio import MANAGED_MARKER, write_marked_text, write_text_atomic
from byor.io.paths import global_config_dir, resolve_repo_root, resolve_within
from byor.rules.rules import Rule, check_id_conflicts, discover_rule_files, load_rules
from byor.rules.skill import SKILL_MARKDOWN, global_skill_paths
from byor.scaffold.ignore import write_rule_visibility_file
from byor.scaffold.sgconfig import ensure_home_sgconfig

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
class CanonicalRules:
    """The canonical global rules, loaded once and reused across repo syncs."""

    root: Path
    rules: list[Rule]


def load_canonical_rules(config_dir: Path) -> CanonicalRules:
    root = global_rules_dir(config_dir, load_global_config(config_dir))
    return CanonicalRules(root=root, rules=load_rules(root))


def compute_sync_plan(
    project: list[Rule],
    local: list[Rule],
    excluded_rule_ids: Iterable[str],
    canonical: CanonicalRules,
) -> SyncPlan:
    """Validate the loaded rules and decide the mirror contents.

    Step 7 (combined effective IDs are unique) holds by construction: project
    and local IDs are unique and disjoint per check_id_conflicts, and every
    kept global rule has an ID outside both sets and unique among global rules.
    """
    check_id_conflicts(project, local, canonical.rules)
    project_ids = {rule.id for rule in project}
    local_ids = {rule.id for rule in local}
    excluded = set(excluded_rule_ids)
    desired: dict[str, str] = {}
    skipped: list[tuple[str, str]] = []
    for rule in canonical.rules:
        reason = _skip_reason(rule.id, project_ids, local_ids, excluded)
        if reason is None:
            desired[rule.path.relative_to(canonical.root).as_posix()] = rule.content
        else:
            skipped.append((rule.id, reason))
    return SyncPlan(desired=desired, skipped=skipped)


def mirror_contents(mirror_dir: Path) -> dict[str, str]:
    """The YAML files currently in the mirror, as relative path -> content."""
    return {
        path.relative_to(mirror_dir).as_posix(): path.read_text(encoding="utf-8")
        for path in discover_rule_files(mirror_dir)
    }


def mirror_global_rules(mirror_dir: Path, desired: dict[str, str]) -> MirrorResult:
    """Make the mirror's YAML contents exactly `desired`.

    Copies new and changed files, deletes YAML files not in `desired`, prunes
    empty subdirectories, and leaves non-YAML files (.gitkeep) alone — except
    the `.ignore` file that keeps the git-ignored copies visible to ast-grep,
    which the mirror restores because the directory is wholly byor-owned.
    """
    write_rule_visibility_file(mirror_dir)
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


def repo_sync_plan(repo_root: Path, canonical: CanonicalRules) -> tuple[SyncPlan, Path]:
    """One repository's sync plan plus its mirror directory."""
    paths = load_repo_config(repo_root).paths
    plan = compute_sync_plan(
        load_rules(repo_root / paths.project_rules),
        load_rules(repo_root / paths.personal_local_rules),
        load_local_config(repo_root).excluded_rule_ids,
        canonical,
    )
    return plan, resolve_within(repo_root, repo_root / paths.personal_global_rules)


def sync_repo(
    repo_root: Path, canonical: CanonicalRules
) -> tuple[SyncPlan, MirrorResult]:
    """Sync one repository: compute the plan and mirror it into place."""
    plan, mirror_dir = repo_sync_plan(repo_root, canonical)
    return plan, mirror_global_rules(mirror_dir, plan.desired)


def repo_is_stale(repo_root: Path, canonical: CanonicalRules) -> bool:
    """The cheap staleness check: path and content comparison."""
    plan, mirror_dir = repo_sync_plan(repo_root, canonical)
    return mirror_contents(mirror_dir) != plan.desired


def heal_global(config_dir: Path) -> None:
    """Keep machine-level state current with the installed byor, silently.

    Runs on every command (even outside a repo): refreshes the global skill
    render so a byor upgrade is reflected without a reinstall, and reconverges
    `~/sgconfig.yml` whenever global rules exist so ast-grep keeps applying them
    everywhere.
    """
    config = load_global_config(config_dir)
    rules_dir = global_rules_dir(config_dir, config)
    if rules_dir.is_dir():
        ensure_home_sgconfig(rules_dir)
    if "skill" in config.agents:
        refresh_skill_renders()


def heal_repo(repo_root: Path, config_dir: Path) -> str | None:
    """The repo self-heal preamble: sync the rule mirror, one line when changed.

    Uninitialized repositories are skipped silently: the command being run
    will fail with its own clearer RepoNotInitialized error.
    """
    if not repo_config_path(repo_root).is_file():
        return None
    _, result = sync_repo(repo_root, load_canonical_rules(config_dir))
    if not result.changed:
        return None
    return f"byor: synced {summarize_changes(result)}"


def refresh_skill_renders() -> None:
    """Rewrite any byor-owned global skill render that drifted from the package.

    byor owns the skill, so running any command keeps the global render current
    with the installed version; an unmarked render a user took over is left
    untouched.
    """
    for path in global_skill_paths():
        write_marked_text(path, SKILL_MARKDOWN, MANAGED_MARKER)


def summarize_changes(result: MirrorResult) -> str:
    """E.g. '2 updated global rules' or '1 updated, 1 removed global rules'."""
    parts: list[str] = []
    if result.written:
        parts.append(f"{result.written} updated")
    if result.removed:
        parts.append(f"{result.removed} removed")
    total = result.written + result.removed
    return f"{', '.join(parts)} global rule{'' if total == 1 else 's'}"


def iter_registered_repos(config_dir: Path) -> Iterator[Path]:
    """Registered repo roots that exist and are initialized, for fan-out.

    Warns on stderr and skips registry entries whose path is gone or that have
    no .byor/config.yml.
    """
    registry_path = repo_registry_path(config_dir, load_global_config(config_dir))
    for repo_root in load_repo_registry(registry_path):
        if not repo_root.is_dir():
            print(
                f"byor: skipping {repo_root}: path no longer exists",
                file=sys.stderr,
            )
        elif not repo_config_path(repo_root).is_file():
            print(
                f"byor: skipping {repo_root}: no .byor/config.yml",
                file=sys.stderr,
            )
        else:
            yield repo_root


def run_sync(args: argparse.Namespace) -> int:
    config_dir = global_config_dir()
    canonical = load_canonical_rules(config_dir)
    if args.all:
        return _sync_all(config_dir, canonical, check=args.check)
    repo_root = resolve_repo_root(explicit=args.repo)
    if args.check:
        return _report_staleness(repo_root, canonical)
    _sync_and_report(repo_root, canonical)
    return 0


def _sync_all(config_dir: Path, canonical: CanonicalRules, check: bool) -> int:
    """Sync (or check) every registered repository."""
    exit_code = 0
    for repo_root in iter_registered_repos(config_dir):
        if check:
            exit_code = max(exit_code, _report_staleness(repo_root, canonical))
        else:
            _sync_and_report(repo_root, canonical)
    return exit_code


def _sync_and_report(repo_root: Path, canonical: CanonicalRules) -> None:
    """Sync one repo and print its sync output."""
    plan, _ = sync_repo(repo_root, canonical)
    print(f"Synced {_count(len(plan.desired), 'global rule')} into {repo_root}")
    if plan.skipped:
        print(f"Skipped {_count(len(plan.skipped), 'global rule')}:")
        for rule_id, reason in plan.skipped:
            print(f"  {rule_id}: {reason}")


def _report_staleness(repo_root: Path, canonical: CanonicalRules) -> int:
    """`sync --check`: report without writing; exit 3 when stale."""
    if repo_is_stale(repo_root, canonical):
        print(f"Sync is stale in {repo_root}; run `byor sync`.")
        return STALE_EXIT_CODE
    print(f"Sync is fresh in {repo_root}")
    return 0


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
        return "excluded in .byor/local.yml"
    return None


def _prune_empty_dirs(root: Path) -> None:
    """Remove empty subdirectories below root, deepest first; keep root itself."""
    # os.walk snapshots entries before children are pruned, so re-check emptiness.
    for dirpath, _, _ in os.walk(root, topdown=False):
        directory = Path(dirpath)
        if directory != root and not any(directory.iterdir()):
            directory.rmdir()


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}{'' if number == 1 else 's'}"
