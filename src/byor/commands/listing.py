"""List effective BYOR rules, checks, and tags.

Listing combines project, local, global, and package sources into a view that explains what
currently applies. The same collection helpers power text and JSON output so humans and automation
see consistent rule metadata.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Literal

from byor.config import load_repo_config
from byor.errors import ByorError
from byor.io.output import write_line, write_lines
from byor.io.paths import display_path, global_config_dir, resolve_repo_root
from byor.rules.rules import load_rules
from byor.rules.sync import SkippedRule, load_canonical_rules, repo_plans
from byor.scan.checks import load_effective_checks

if TYPE_CHECKING:
    import argparse
    from pathlib import Path

__all__ = (
    "ListedCheck",
    "ListedRule",
    "TagSummary",
    "collect_checks",
    "collect_rules",
    "collect_skipped",
    "render_listing",
    "render_tags",
    "run_list",
)

ListScope = Literal["project", "local", "global", "package", "effective", "all"]


@dataclass
class ListedRule:
    scope: str
    id: str
    path: str
    """Repo-relative POSIX path of the rule file ast-grep reads."""
    tags: list[str]


@dataclass
class ListedCheck:
    name: str
    origin: str
    run: str
    tags: list[str]


@dataclass
class TagSummary:
    kind: str
    tag: str
    count: int
    origins: dict[str, int]


def run_list(args: argparse.Namespace) -> int:
    repo_root = resolve_repo_root(explicit=args.repo)
    config_dir = global_config_dir()
    scope: ListScope = args.scope
    rules = collect_rules(repo_root, scope)
    skipped = collect_skipped(repo_root, config_dir) if scope == "all" else []
    checks = collect_checks(repo_root, config_dir)
    rules, skipped, checks = _filter_by_tags(rules, skipped, checks=checks, args=args)
    if args.json:
        payload = (
            _tag_json_payload(rules, skipped, checks=checks)
            if args.tags
            else _json_payload(rules, skipped, checks=checks)
        )
        write_line(json.dumps(payload, indent=2))
    elif args.tags:
        write_lines(render_tags(rules, skipped, checks=checks) or ["No tags found."])
    else:
        listing = render_listing(rules, skipped, checks=checks)
        empty = ["No rules or checks yet. Add a rule with `byor add`."]
        write_lines(listing or empty)
    return 0


def collect_rules(repo_root: Path, scope: ListScope) -> list[ListedRule]:
    """Rules in display order: project, local, synced global, then package copies.

    The `global` and `package` rows are the mirrored copies ast-grep actually
    reads; after the self-heal preamble they match their canonical sources minus
    skips.
    """
    paths = load_repo_config(repo_root).paths
    directories = {
        "project": paths.project_rules,
        "local": paths.personal_local_rules,
        "global": paths.personal_global_rules,
        "package": paths.personal_packages_rules,
    }
    wanted = ("project", "local", "global", "package") if scope in ("effective", "all") else (scope,)
    return [
        ListedRule(
            scope=name,
            id=rule.id,
            path=display_path(rule.path, repo_root),
            tags=list(rule.tags),
        )
        for name in wanted
        for rule in load_rules(repo_root / directories[name])
    ]


def collect_skipped(repo_root: Path, config_dir: Path) -> list[SkippedRule]:
    return repo_plans(repo_root, load_canonical_rules(config_dir)).global_plan.skipped


def collect_checks(repo_root: Path, config_dir: Path) -> list[ListedCheck]:
    return [
        ListedCheck(
            name=check.name,
            origin=check.origin,
            run=check.definition.run,
            tags=list(check.definition.tags),
        )
        for check in load_effective_checks(repo_root, config_dir)
    ]


def render_listing(
    rules: list[ListedRule],
    skipped: list[SkippedRule],
    *,
    checks: list[ListedCheck],
) -> list[str]:
    rows = [(rule.scope, rule.id, rule.path) for rule in rules]
    rows += [("skipped", rule.id, rule.reason) for rule in skipped]
    rows += [(f"check/{check.origin}", check.name, check.run) for check in checks]
    if not rows:
        return []
    scope_width = max(len(scope) for scope, _, _ in rows)
    id_width = max(len(rule_id) for _, rule_id, _ in rows)
    return [f"{scope:<{scope_width}}  {rule_id:<{id_width}}  {detail}" for scope, rule_id, detail in rows]


def render_tags(
    rules: list[ListedRule],
    skipped: list[SkippedRule],
    *,
    checks: list[ListedCheck],
) -> list[str]:
    rows = [
        (summary.kind, summary.tag, str(summary.count), _origins(summary.origins))
        for summary in _tag_summaries(rules, skipped, checks=checks)
    ]
    if not rows:
        return []
    kind_width = max(len(kind) for kind, _, _, _ in rows)
    tag_width = max(len(tag) for _, tag, _, _ in rows)
    count_width = max(len(count) for _, _, count, _ in rows)
    return [
        f"{kind:<{kind_width}}  {tag:<{tag_width}}  {count:>{count_width}}  {origins}"
        for kind, tag, count, origins in rows
    ]


def _json_payload(
    rules: list[ListedRule],
    skipped: list[SkippedRule],
    *,
    checks: list[ListedCheck],
) -> dict[str, list[dict[str, str | list[str]]]]:
    return {
        "rules": [asdict(rule) for rule in rules],
        "skipped": [asdict(rule) for rule in skipped],
        "checks": [asdict(check) for check in checks],
    }


def _tag_json_payload(
    rules: list[ListedRule],
    skipped: list[SkippedRule],
    *,
    checks: list[ListedCheck],
) -> dict[str, list[dict[str, str | int | dict[str, int]]]]:
    return {"tags": [asdict(summary) for summary in _tag_summaries(rules, skipped, checks=checks)]}


def _filter_by_tags(
    rules: list[ListedRule],
    skipped: list[SkippedRule],
    *,
    checks: list[ListedCheck],
    args: argparse.Namespace,
) -> tuple[list[ListedRule], list[SkippedRule], list[ListedCheck]]:
    tag = getattr(args, "tag", None)
    if getattr(args, "tags", False) and tag is not None:
        msg = "--tags cannot be combined with --tag"
        raise ByorError(msg)
    if tag is None:
        return rules, skipped, checks
    rules = [rule for rule in rules if tag in rule.tags]
    skipped = [rule for rule in skipped if tag in rule.tags]
    checks = [check for check in checks if tag in check.tags]
    return rules, skipped, checks


def _tag_summaries(
    rules: list[ListedRule],
    skipped: list[SkippedRule],
    *,
    checks: list[ListedCheck],
) -> list[TagSummary]:
    counts: dict[tuple[str, str], Counter[str]] = {}
    for rule in rules:
        _count_tags(counts, "rule", origin=rule.scope, tags=rule.tags)
    for rule in skipped:
        _count_tags(counts, "rule", origin="skipped", tags=rule.tags)
    for check in checks:
        _count_tags(counts, "check", origin=check.origin, tags=check.tags)
    return [
        TagSummary(
            kind=kind,
            tag=tag,
            count=sum(origins.values()),
            origins=dict(sorted(origins.items())),
        )
        for (kind, tag), origins in sorted(counts.items())
    ]


def _count_tags(
    counts: dict[tuple[str, str], Counter[str]],
    kind: str,
    *,
    origin: str,
    tags: list[str],
) -> None:
    for tag in tags:
        counts.setdefault((kind, tag), Counter())[origin] += 1


def _origins(origins: dict[str, int]) -> str:
    return ", ".join(f"{origin}:{count}" for origin, count in origins.items())
