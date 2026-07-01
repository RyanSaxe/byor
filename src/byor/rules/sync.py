"""Mirror canonical global rules into repositories.

Sync computes the effective global and package rules for a repository, writes managed mirror
directories, and reports staleness. It is the convergence engine shared by init, sync, profile,
package, and self-heal flows.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from byor.agents.install import install_agent
from byor.config import (
    PACKAGE_CHECKS_FILE,
    LocalConfig,
    global_packages_dir,
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
from byor.rules.rules import (
    Rule,
    check_id_conflicts,
    discover_rule_files,
    load_rule,
    load_rules,
    require_unique_ids,
)
from byor.rules.skill import global_skill_dirs, skill_files
from byor.scaffold.ignore import write_rule_visibility_file
from byor.scaffold.sgconfig import ensure_home_sgconfig

if TYPE_CHECKING:
    import argparse
    from collections.abc import Iterable, Iterator

__all__ = (
    "CanonicalRules",
    "InstalledPackage",
    "MirrorResult",
    "SkippedRule",
    "SyncPlan",
    "compute_packages_plan",
    "compute_sync_plan",
    "heal_global",
    "heal_repo",
    "iter_registered_repos",
    "load_canonical_rules",
    "load_installed_packages",
    "mirror_contents",
    "mirror_global_rules",
    "refresh_skill_renders",
    "repo_is_stale",
    "repo_packages_plan",
    "repo_sync_plan",
    "run_sync",
    "summarize_changes",
    "sync_repo",
)

STALE_EXIT_CODE = 3


@dataclass
class SkippedRule:
    id: str
    reason: str
    tags: list[str]


@dataclass
class SyncPlan:
    desired: dict[str, str]
    """Relative path below the global rules root -> rule file content."""

    skipped: list[SkippedRule]
    """Skipped canonical global rules, in canonical discovery order."""


@dataclass
class MirrorResult:
    written: int
    removed: int

    @property
    def changed(self) -> bool:
        return self.written > 0 or self.removed > 0


@dataclass
class CanonicalRules:
    root: Path
    rules: list[Rule]
    packages_root: Path


@dataclass
class InstalledPackage:
    name: str
    root: Path
    rules: list[Rule]


def load_canonical_rules(config_dir: Path) -> CanonicalRules:
    config = load_global_config(config_dir)
    root = global_rules_dir(config_dir, config)
    return CanonicalRules(
        root=root,
        rules=load_rules(root),
        packages_root=global_packages_dir(config_dir, config),
    )


def load_installed_packages(canonical: CanonicalRules, names: Iterable[str]) -> list[InstalledPackage]:
    return [
        InstalledPackage(
            name=name,
            root=canonical.packages_root / name,
            rules=_load_package_rules(canonical.packages_root / name),
        )
        for name in names
    ]


def _load_package_rules(root: Path) -> list[Rule]:
    return [
        load_rule(path)
        for path in discover_rule_files(root)
        if not (path.parent == root and path.name == PACKAGE_CHECKS_FILE)
    ]


def _effective_canonical(
    project: list[Rule],
    local: list[Rule],
    *,
    excluded_rule_ids: Iterable[str],
    excluded_tags: Iterable[str],
    canonical: CanonicalRules,
) -> tuple[list[Rule], list[SkippedRule]]:
    """Return the canonical global rules a repo keeps and skip the rest.

    Step 7 (combined effective IDs are unique) holds by construction: project
    and local IDs are unique and disjoint per check_id_conflicts, and every
    kept global rule has an ID outside both sets and unique among global rules.
    """
    check_id_conflicts(project, local, canonical_global=canonical.rules)
    project_ids = {rule.id for rule in project}
    local_ids = {rule.id for rule in local}
    excluded = set(excluded_rule_ids)
    excluded_tag_set = set(excluded_tags)
    kept: list[Rule] = []
    skipped: list[SkippedRule] = []
    for rule in canonical.rules:
        reason = _skip_reason(rule, project_ids, local_ids=local_ids, excluded=excluded, excluded_tags=excluded_tag_set)
        if reason is None:
            kept.append(rule)
        else:
            skipped.append(SkippedRule(rule.id, reason, list(rule.tags)))
    return kept, skipped


def compute_sync_plan(
    project: list[Rule],
    local: list[Rule],
    *,
    excluded_rule_ids: Iterable[str],
    excluded_tags: Iterable[str],
    canonical: CanonicalRules,
) -> SyncPlan:
    kept, skipped = _effective_canonical(
        project, local, excluded_rule_ids=excluded_rule_ids, excluded_tags=excluded_tags, canonical=canonical
    )
    desired = {rule.path.relative_to(canonical.root).as_posix(): rule.content for rule in kept}
    return SyncPlan(desired=desired, skipped=skipped)


def compute_packages_plan(
    project: list[Rule],
    local: list[Rule],
    *,
    local_config: LocalConfig,
    kept_global_ids: set[str],
    packages: list[InstalledPackage],
) -> SyncPlan:
    """Decide the packages mirror contents, keyed `<package>/<rule-path>`.

    A package rule an owned scope already provides (project, local, or a kept
    global rule) is skipped, as is one excluded by ID or tag. Two installed
    packages defining the same surviving ID is a hard error: ast-grep rejects
    duplicate IDs, so byor names both and points at `byor exclude`.
    """
    project_ids = {rule.id for rule in project}
    local_ids = {rule.id for rule in local}
    excluded = set(local_config.excluded_rule_ids)
    excluded_tags = set(local_config.excluded_rule_tags)
    desired: dict[str, str] = {}
    skipped: list[SkippedRule] = []
    kept: list[Rule] = []
    for package in packages:
        for rule in package.rules:
            reason = _package_skip_reason(
                rule,
                project_ids,
                local_ids=local_ids,
                global_ids=kept_global_ids,
                excluded=excluded,
                excluded_tags=excluded_tags,
            )
            if reason is not None:
                skipped.append(SkippedRule(rule.id, reason, list(rule.tags)))
                continue
            relpath = rule.path.relative_to(package.root).as_posix()
            desired[f"{package.name}/{relpath}"] = rule.content
            kept.append(rule)
    require_unique_ids(
        kept,
        "installed package rules",
        hint="Two installed packages define this ID; exclude one with `byor exclude`.",
    )
    return SyncPlan(desired=desired, skipped=skipped)


def mirror_contents(mirror_dir: Path) -> dict[str, str]:
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
    paths = load_repo_config(repo_root).paths
    local_config = load_local_config(repo_root)
    plan = compute_sync_plan(
        load_rules(repo_root / paths.project_rules),
        load_rules(repo_root / paths.personal_local_rules),
        excluded_rule_ids=local_config.excluded_rule_ids,
        excluded_tags=local_config.excluded_rule_tags,
        canonical=canonical,
    )
    return plan, resolve_within(repo_root, repo_root / paths.personal_global_rules)


def repo_packages_plan(repo_root: Path, canonical: CanonicalRules) -> tuple[SyncPlan, Path]:
    paths = load_repo_config(repo_root).paths
    local_config = load_local_config(repo_root)
    project = load_rules(repo_root / paths.project_rules)
    local = load_rules(repo_root / paths.personal_local_rules)
    kept_global, _ = _effective_canonical(
        project,
        local,
        excluded_rule_ids=local_config.excluded_rule_ids,
        excluded_tags=local_config.excluded_rule_tags,
        canonical=canonical,
    )
    plan = compute_packages_plan(
        project,
        local,
        local_config=local_config,
        kept_global_ids={rule.id for rule in kept_global},
        packages=load_installed_packages(canonical, local_config.packages),
    )
    return plan, resolve_within(repo_root, repo_root / paths.personal_packages_rules)


def sync_repo(repo_root: Path, canonical: CanonicalRules) -> tuple[SyncPlan, MirrorResult]:
    global_plan, global_dir = repo_sync_plan(repo_root, canonical)
    global_result = mirror_global_rules(global_dir, global_plan.desired)
    packages_plan, packages_dir = repo_packages_plan(repo_root, canonical)
    packages_result = mirror_global_rules(packages_dir, packages_plan.desired)
    # `desired` stays the global mirror alone: it feeds the "N global rules"
    # sync count. Package changes still register through the mirror result.
    plan = SyncPlan(
        desired=global_plan.desired,
        skipped=global_plan.skipped + packages_plan.skipped,
    )
    result = MirrorResult(
        written=global_result.written + packages_result.written,
        removed=global_result.removed + packages_result.removed,
    )
    return plan, result


def repo_is_stale(repo_root: Path, canonical: CanonicalRules) -> bool:
    global_plan, global_dir = repo_sync_plan(repo_root, canonical)
    packages_plan, packages_dir = repo_packages_plan(repo_root, canonical)
    return mirror_contents(global_dir) != global_plan.desired or mirror_contents(packages_dir) != packages_plan.desired


def heal_global(config_dir: Path) -> None:
    """Keep machine-level state current with the installed byor, silently.

    Runs on every command (even outside a repo): refreshes the global skill
    render so a byor upgrade is reflected without a reinstall, and reconverges
    recorded agent hooks/plugins plus `~/sgconfig.yml` so upgrades keep applying
    everywhere.
    """
    config = load_global_config(config_dir)
    rules_dir = global_rules_dir(config_dir, config)
    if rules_dir.is_dir():
        ensure_home_sgconfig(rules_dir)
    for agent in config.agents:
        install_agent(agent)


def heal_repo(repo_root: Path, config_dir: Path) -> str | None:
    if not repo_config_path(repo_root).is_file():
        return None
    _, result = sync_repo(repo_root, load_canonical_rules(config_dir))
    if not result.changed:
        return None
    return f"byor: synced {summarize_changes(result)}"


def refresh_skill_renders() -> None:
    """Rewrite any byor-owned global skill file that drifted from the package.

    byor owns the skill, so running any command keeps the global tree current
    with the installed version; an unmarked file a user took over is left
    untouched.
    """
    for base in global_skill_dirs():
        for relpath, content in skill_files():
            write_marked_text(base / relpath, content, marker=MANAGED_MARKER)


def summarize_changes(result: MirrorResult) -> str:
    parts: list[str] = []
    if result.written:
        parts.append(f"{result.written} updated")
    if result.removed:
        parts.append(f"{result.removed} removed")
    total = result.written + result.removed
    return f"{', '.join(parts)} global rule{'' if total == 1 else 's'}"


def iter_registered_repos(config_dir: Path) -> Iterator[Path]:
    registry_path = repo_registry_path(config_dir, load_global_config(config_dir))
    for repo_root in load_repo_registry(registry_path):
        if not repo_root.is_dir():
            sys.stderr.write(f"byor: skipping {repo_root}: path no longer exists\n")
        elif not repo_config_path(repo_root).is_file():
            sys.stderr.write(f"byor: skipping {repo_root}: no .byor/config.yml\n")
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


def _sync_all(config_dir: Path, canonical: CanonicalRules, *, check: bool) -> int:
    exit_code = 0
    for repo_root in iter_registered_repos(config_dir):
        if check:
            exit_code = max(exit_code, _report_staleness(repo_root, canonical))
        else:
            _sync_and_report(repo_root, canonical)
    return exit_code


def _sync_and_report(repo_root: Path, canonical: CanonicalRules) -> None:
    plan, _ = sync_repo(repo_root, canonical)
    sys.stdout.write(f"Synced {_count(len(plan.desired), 'global rule')} into {repo_root}\n")
    if plan.skipped:
        sys.stdout.write(f"Skipped {_count(len(plan.skipped), 'global rule')}:\n")
        for skipped in plan.skipped:
            sys.stdout.write(f"  {skipped.id}: {skipped.reason}\n")


def _report_staleness(repo_root: Path, canonical: CanonicalRules) -> int:
    if repo_is_stale(repo_root, canonical):
        sys.stdout.write(f"Sync is stale in {repo_root}; run `byor sync`.\n")
        return STALE_EXIT_CODE
    sys.stdout.write(f"Sync is fresh in {repo_root}\n")
    return 0


def _skip_reason(
    rule: Rule,
    project_ids: set[str],
    *,
    local_ids: set[str],
    excluded: set[str],
    excluded_tags: set[str],
) -> str | None:
    if rule.id in project_ids:
        return "overridden by project rule"
    if rule.id in local_ids:
        return "overridden by local rule"
    if rule.id in excluded:
        return "excluded in .byor/local.yml"
    for tag in rule.tags:
        if tag in excluded_tags:
            return f"excluded by tag '{tag}' in .byor/local.yml"
    return None


def _package_skip_reason(
    rule: Rule,
    project_ids: set[str],
    *,
    local_ids: set[str],
    global_ids: set[str],
    excluded: set[str],
    excluded_tags: set[str],
) -> str | None:
    if rule.id in project_ids:
        return "overridden by project rule"
    if rule.id in local_ids:
        return "overridden by local rule"
    if rule.id in global_ids:
        return "overridden by global rule"
    if rule.id in excluded:
        return "excluded in .byor/local.yml"
    for tag in rule.tags:
        if tag in excluded_tags:
            return f"excluded by tag '{tag}' in .byor/local.yml"
    return None


def _prune_empty_dirs(root: Path) -> None:
    # os.walk snapshots entries before children are pruned, so re-check emptiness.
    for dirpath, _, _ in os.walk(root, topdown=False):
        directory = Path(dirpath)
        if directory != root and not any(directory.iterdir()):
            directory.rmdir()


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}{'' if number == 1 else 's'}"
