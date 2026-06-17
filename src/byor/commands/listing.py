"""`byor list`: show rules and where they come from."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from byor.config import load_repo_config
from byor.io.paths import display_path, global_config_dir, resolve_repo_root
from byor.rules.rules import load_rules
from byor.rules.sync import load_canonical_rules, repo_sync_plan
from byor.scan.checks import EffectiveCheck, load_effective_checks

ListScope = Literal["project", "local", "global", "effective", "all"]


@dataclass
class ListedRule:
    scope: str
    id: str
    path: str
    """Repo-relative POSIX path of the rule file ast-grep reads."""


def run_list(args: argparse.Namespace) -> int:
    repo_root = resolve_repo_root(explicit=args.repo)
    config_dir = global_config_dir()
    scope: ListScope = args.scope
    rules = collect_rules(repo_root, scope)
    skipped = collect_skipped(repo_root, config_dir) if scope == "all" else []
    checks = load_effective_checks(repo_root, config_dir)
    if args.json:
        print(json.dumps(_json_payload(rules, skipped, checks), indent=2))
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
        )
        for name in wanted
        for rule in load_rules(repo_root / directories[name])
    ]


def collect_skipped(repo_root: Path, config_dir: Path) -> list[tuple[str, str]]:
    """(rule ID, reason) for canonical global rules sync does not mirror."""
    plan, _ = repo_sync_plan(repo_root, load_canonical_rules(config_dir))
    return plan.skipped


def render_listing(
    rules: list[ListedRule],
    skipped: list[tuple[str, str]],
    checks: list[EffectiveCheck],
) -> list[str]:
    rows = [(rule.scope, rule.id, rule.path) for rule in rules]
    rows += [("skipped", rule_id, reason) for rule_id, reason in skipped]
    rows += [
        (f"check/{check.origin}", check.name, check.definition.run) for check in checks
    ]
    if not rows:
        return []
    scope_width = max(len(scope) for scope, _, _ in rows)
    id_width = max(len(rule_id) for _, rule_id, _ in rows)
    return [
        f"{scope:<{scope_width}}  {rule_id:<{id_width}}  {detail}"
        for scope, rule_id, detail in rows
    ]


def _json_payload(
    rules: list[ListedRule],
    skipped: list[tuple[str, str]],
    checks: list[EffectiveCheck],
) -> dict[str, list[dict[str, str]]]:
    return {
        "rules": [asdict(rule) for rule in rules],
        "skipped": [{"id": rule_id, "reason": reason} for rule_id, reason in skipped],
        "checks": [
            {"name": check.name, "origin": check.origin, "run": check.definition.run}
            for check in checks
        ],
    }
