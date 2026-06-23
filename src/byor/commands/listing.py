"""`byor list`: show rules and where they come from."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from byor.config import load_repo_config
from byor.errors import ByorError
from byor.io.paths import display_path, global_config_dir, resolve_repo_root
from byor.rules.rules import load_rules
from byor.rules.sync import SkippedRule, load_canonical_rules, repo_sync_plan
from byor.scan.checks import load_effective_checks

ListScope = Literal["project", "local", "global", "effective", "all"]


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
    rules, skipped, checks = _filter_by_tags(rules, skipped, checks, args)
    if args.json:
        payload = (
            _tag_json_payload(rules, skipped, checks)
            if args.tags
            else _json_payload(rules, skipped, checks)
        )
        print(json.dumps(payload, indent=2))
    elif args.tags:
        for line in render_tags(rules, skipped, checks) or ["No tags found."]:
            print(line)
    else:
        listing = render_listing(rules, skipped, checks)
        empty = ["No rules or checks yet. Add a rule with `byor add`."]
        for line in listing or empty:
            print(line)
    return 0


def collect_rules(repo_root: Path, scope: ListScope) -> list[ListedRule]:
    """Rules in display order: project, then local, then synced global copies.

    The `global` rows are the mirrored copies ast-grep actually reads; after
    the self-heal preamble they match the canonical rules minus skips.
    """
    paths = load_repo_config(repo_root).paths
    directories = {
        "project": paths.project_rules,
        "local": paths.personal_local_rules,
        "global": paths.personal_global_rules,
    }
    wanted = (
        ("project", "local", "global") if scope in ("effective", "all") else (scope,)
    )
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
    """Canonical global rules sync does not mirror."""
    plan, _ = repo_sync_plan(repo_root, load_canonical_rules(config_dir))
    return plan.skipped


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
    checks: list[ListedCheck],
) -> list[str]:
    rows = [(rule.scope, rule.id, rule.path) for rule in rules]
    rows += [("skipped", rule.id, rule.reason) for rule in skipped]
    rows += [(f"check/{check.origin}", check.name, check.run) for check in checks]
    if not rows:
        return []
    scope_width = max(len(scope) for scope, _, _ in rows)
    id_width = max(len(rule_id) for _, rule_id, _ in rows)
    return [
        f"{scope:<{scope_width}}  {rule_id:<{id_width}}  {detail}"
        for scope, rule_id, detail in rows
    ]


def render_tags(
    rules: list[ListedRule],
    skipped: list[SkippedRule],
    checks: list[ListedCheck],
) -> list[str]:
    rows = [
        (summary.kind, summary.tag, str(summary.count), _origins(summary.origins))
        for summary in _tag_summaries(rules, skipped, checks)
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
    checks: list[ListedCheck],
) -> dict[str, list[dict[str, str | int | dict[str, int]]]]:
    return {
        "tags": [asdict(summary) for summary in _tag_summaries(rules, skipped, checks)]
    }


def _filter_by_tags(
    rules: list[ListedRule],
    skipped: list[SkippedRule],
    checks: list[ListedCheck],
    args: argparse.Namespace,
) -> tuple[list[ListedRule], list[SkippedRule], list[ListedCheck]]:
    rule_tag = getattr(args, "tag", None)
    check_tag = getattr(args, "check_tag", None)
    if getattr(args, "tags", False) and (rule_tag is not None or check_tag is not None):
        raise ByorError("--tags cannot be combined with --tag or --check-tag")
    if rule_tag is not None:
        rules = [rule for rule in rules if rule_tag in rule.tags]
        skipped = [rule for rule in skipped if rule_tag in rule.tags]
        if check_tag is None:
            checks = []
    if check_tag is not None:
        checks = [check for check in checks if check_tag in check.tags]
        if rule_tag is None:
            rules = []
            skipped = []
    return rules, skipped, checks


def _tag_summaries(
    rules: list[ListedRule],
    skipped: list[SkippedRule],
    checks: list[ListedCheck],
) -> list[TagSummary]:
    counts: dict[tuple[str, str], dict[str, int]] = {}
    for rule in rules:
        _count_tags(counts, "rule", rule.scope, rule.tags)
    for rule in skipped:
        _count_tags(counts, "rule", "skipped", rule.tags)
    for check in checks:
        _count_tags(counts, "check", check.origin, check.tags)
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
    counts: dict[tuple[str, str], dict[str, int]],
    kind: str,
    origin: str,
    tags: list[str],
) -> None:
    for tag in tags:
        origins = counts.setdefault((kind, tag), {})
        origins[origin] = origins.get(origin, 0) + 1


def _origins(origins: dict[str, int]) -> str:
    return ", ".join(f"{origin}:{count}" for origin, count in origins.items())
