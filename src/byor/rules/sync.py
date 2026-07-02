"""Mirror canonical global rules into repositories.

Sync computes the effective global and package rules for a repository, writes managed mirror
directories, and reports staleness. It is the convergence engine shared by init, sync, profile,
package, and self-heal flows.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING

from byor.config import (
    PACKAGE_CHECKS_FILE,
    LocalConfig,
    disabled_entry,
    global_packages_dir,
    global_rules_dir,
    load_global_config,
    load_local_config,
    load_repo_config,
    load_repo_registry,
    repo_config_path,
    repo_registry_path,
)
from byor.errors import ByorError
from byor.io.fsio import prune_empty_dirs, write_text_atomic
from byor.io.paths import global_config_dir, resolve_repo_root, resolve_within
from byor.rules.rules import (
    Rule,
    check_id_conflicts,
    discover_rule_files,
    load_rule,
    load_rules,
    require_unique_ids,
)
from byor.scaffold.ignore import write_rule_visibility_file

if TYPE_CHECKING:
    import argparse
    from collections.abc import Iterable, Iterator
    from pathlib import Path

__all__ = (
    "CanonicalRules",
    "InstalledPackage",
    "MirrorResult",
    "RepoPlans",
    "SkippedRule",
    "SyncPlan",
    "compute_packages_plan",
    "iter_registered_repos",
    "load_canonical_rules",
    "load_installed_packages",
    "mirror_contents",
    "mirror_global_rules",
    "repo_is_stale",
    "repo_plans",
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
    package_ids: set[str],
    excluded_rule_ids: Iterable[str],
    excluded_tags: Iterable[str],
    canonical: CanonicalRules,
) -> tuple[list[Rule], list[SkippedRule]]:
    """Return the canonical global rules a repo keeps and skip the rest.

    Step 7 (combined effective IDs are unique) holds by construction: project
    and local IDs are unique and disjoint per check_id_conflicts, `package_ids`
    holds the surviving package rules (already outside project and local), and
    every kept global rule has an ID outside all three sets and unique among
    global rules.
    """
    check_id_conflicts(project, local, canonical_global=canonical.rules)
    project_ids = {rule.id for rule in project}
    local_ids = {rule.id for rule in local}
    excluded = set(excluded_rule_ids)
    excluded_tag_set = set(excluded_tags)
    kept: list[Rule] = []
    skipped: list[SkippedRule] = []
    for rule in canonical.rules:
        reason = _skip_reason(
            rule,
            project_ids,
            local_ids=local_ids,
            package_ids=package_ids,
            excluded=excluded,
            excluded_tags=excluded_tag_set,
        )
        if reason is None:
            kept.append(rule)
        else:
            skipped.append(SkippedRule(rule.id, reason, list(rule.tags)))
    return kept, skipped


def _global_plan(kept: list[Rule], skipped: list[SkippedRule], *, root: Path) -> SyncPlan:
    desired = {rule.path.relative_to(root).as_posix(): rule.content for rule in kept}
    return SyncPlan(desired=desired, skipped=skipped)


def compute_packages_plan(
    project: list[Rule],
    local: list[Rule],
    *,
    local_config: LocalConfig,
    packages: list[InstalledPackage],
) -> tuple[SyncPlan, set[str]]:
    """Decide the packages mirror contents, keyed `<package>/<rule-path>`.

    A package rule a repo-owned scope already provides (project or local) is
    skipped, as is one excluded by ID or tag; a surviving package rule wins
    over a same-ID global rule, so the second returned value — the surviving
    IDs — is what the global plan skips. Two installed packages defining the
    same surviving ID is a hard error: ast-grep rejects duplicate IDs, so byor
    names both and points at `byor exclude`.
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
            reason = _skip_reason(
                rule,
                project_ids,
                local_ids=local_ids,
                package_ids=set(),
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
    return SyncPlan(desired=desired, skipped=skipped), {rule.id for rule in kept}


def mirror_contents(mirror_dir: Path) -> dict[str, str]:
    contents: dict[str, str] = {}
    for path in discover_rule_files(mirror_dir):
        text = _read_if_present(path)
        if text is not None:
            contents[path.relative_to(mirror_dir).as_posix()] = text
    return contents


def _read_if_present(path: Path) -> str | None:
    # A concurrent sync (byor's own git-hook shims run `byor sync`) may remove
    # a file between discovery and read; skip it and converge on the next heal.
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None


def mirror_global_rules(mirror_dir: Path, desired: dict[str, str]) -> MirrorResult:
    """Make the mirror's YAML contents exactly `desired`.

    Copies new and changed files, deletes YAML files not in `desired`, prunes
    empty subdirectories, and leaves non-YAML files (.gitkeep) alone — except
    the `.ignore` file that keeps the git-ignored copies visible to ast-grep,
    which the mirror restores because the directory is wholly byor-owned.
    """
    write_rule_visibility_file(mirror_dir, force=True)
    actual = mirror_contents(mirror_dir)
    removed = 0
    # Remove before writing: after a case-only rename, a case-insensitive
    # filesystem resolves the stale old name to the same file as the new one,
    # so writing first would let this loop delete the just-written rule.
    for relpath in actual:
        if relpath not in desired:
            (mirror_dir / relpath).unlink(missing_ok=True)  # concurrent syncs race here
            removed += 1
    written = 0
    for relpath, content in desired.items():
        if actual.get(relpath) != content:
            write_text_atomic(mirror_dir / relpath, content)
            written += 1
    prune_empty_dirs(mirror_dir)
    return MirrorResult(written=written, removed=removed)


@dataclass
class RepoPlans:
    global_plan: SyncPlan
    global_dir: Path
    packages_plan: SyncPlan
    packages_dir: Path


def repo_plans(repo_root: Path, canonical: CanonicalRules) -> RepoPlans:
    """Compute the global and packages mirror plans in one pass.

    Loads the repo config, local config, and both rule scopes once and reuses
    the effective-canonical result for both plans, so a self-heal does not read
    and parse every rule file twice.
    """
    paths = load_repo_config(repo_root).paths
    local_config = load_local_config(repo_root)
    project = load_rules(repo_root / paths.project_rules)
    local = load_rules(repo_root / paths.personal_local_rules)
    packages_plan, package_ids = compute_packages_plan(
        project,
        local,
        local_config=local_config,
        packages=load_installed_packages(canonical, local_config.packages),
    )
    kept, skipped = _effective_canonical(
        project,
        local,
        package_ids=package_ids,
        excluded_rule_ids=local_config.excluded_rule_ids,
        excluded_tags=local_config.excluded_rule_tags,
        canonical=canonical,
    )
    return RepoPlans(
        global_plan=_global_plan(kept, skipped, root=canonical.root),
        global_dir=resolve_within(repo_root, repo_root / paths.personal_global_rules),
        packages_plan=packages_plan,
        packages_dir=resolve_within(repo_root, repo_root / paths.personal_packages_rules),
    )


def sync_repo(repo_root: Path, canonical: CanonicalRules) -> tuple[SyncPlan, MirrorResult]:
    plans = repo_plans(repo_root, canonical)
    global_result = mirror_global_rules(plans.global_dir, plans.global_plan.desired)
    packages_result = mirror_global_rules(plans.packages_dir, plans.packages_plan.desired)
    # `desired` stays the global mirror alone: it feeds the "N global rules"
    # sync count. Package changes still register through the mirror result.
    plan = SyncPlan(
        desired=plans.global_plan.desired,
        skipped=plans.global_plan.skipped + plans.packages_plan.skipped,
    )
    result = MirrorResult(
        written=global_result.written + packages_result.written,
        removed=global_result.removed + packages_result.removed,
    )
    return plan, result


def repo_is_stale(repo_root: Path, canonical: CanonicalRules) -> bool:
    plans = repo_plans(repo_root, canonical)
    return (
        mirror_contents(plans.global_dir) != plans.global_plan.desired
        or mirror_contents(plans.packages_dir) != plans.packages_plan.desired
    )


def summarize_changes(result: MirrorResult) -> str:
    parts: list[str] = []
    if result.written:
        parts.append(f"{result.written} updated")
    if result.removed:
        parts.append(f"{result.removed} removed")
    total = result.written + result.removed
    return f"{', '.join(parts)} global rule{'' if total == 1 else 's'}"


def iter_registered_repos(config_dir: Path) -> Iterator[Path]:
    config = load_global_config(config_dir)
    for repo_root in load_repo_registry(repo_registry_path(config_dir, config)):
        if not repo_root.is_dir():
            sys.stderr.write(f"byor: skipping {repo_root}: path no longer exists\n")
        elif disabled_entry(repo_root, config) is not None:
            sys.stderr.write(f"byor: skipping {repo_root}: disabled for byor (run `byor enable`)\n")
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
        # One broken repo must not abort the fan-out: warn and keep going.
        try:
            if check:
                exit_code = max(exit_code, _report_staleness(repo_root, canonical))
            else:
                _sync_and_report(repo_root, canonical)
        except ByorError as error:
            sys.stderr.write(f"byor: skipping {repo_root}: {error} (run 'byor doctor')\n")
            exit_code = max(exit_code, error.exit_code)
    return exit_code


def _sync_and_report(repo_root: Path, canonical: CanonicalRules) -> None:
    plan, result = sync_repo(repo_root, canonical)
    # A steady-state sync (the post-merge/post-checkout shims run one on every
    # pull) must not narrate forever: silence is the unix success signal, so a
    # sync that changed nothing prints nothing; `byor list` keeps skips
    # visible on demand.
    if not result.changed:
        return
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
    package_ids: set[str],
    excluded: set[str],
    excluded_tags: set[str],
) -> str | None:
    # Precedence order: project > local > package > global. Package rules pass
    # an empty `package_ids`; canonical global rules pass the surviving package
    # IDs so an opted-in package overrides the global copy.
    if rule.id in project_ids:
        return "overridden by project rule"
    if rule.id in local_ids:
        return "overridden by local rule"
    if rule.id in package_ids:
        return "overridden by package rule"
    if rule.id in excluded:
        return "excluded in .byor/local.yml"
    for tag in rule.tags:
        if tag in excluded_tags:
            return f"excluded by tag '{tag}' in .byor/local.yml"
    return None


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}{'' if number == 1 else 's'}"
