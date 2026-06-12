"""Rule-mutating commands: add, edit, promote, exclude, include (SPEC 15.4-15.7)."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from byolsp.config import (
    RepoPaths,
    global_rules_dir,
    load_global_config,
    load_local_config,
    load_repo_config,
    save_local_config,
)
from byolsp.paths import global_config_dir, resolve_repo_root
from byolsp.sync import SyncPlan, load_canonical_rules, summarize_changes, sync_repo


@dataclass
class RepoContext:
    """The resolved locations every rule command needs."""

    repo_root: Path
    config_dir: Path
    paths: RepoPaths
    global_rules_root: Path


def repo_context(args: argparse.Namespace) -> RepoContext:
    """Resolve the repo and global locations; fails on uninitialized repos."""
    repo_root = resolve_repo_root(explicit=args.repo)
    config_dir = global_config_dir()
    return RepoContext(
        repo_root=repo_root,
        config_dir=config_dir,
        paths=load_repo_config(repo_root).paths,
        global_rules_root=global_rules_dir(config_dir, load_global_config(config_dir)),
    )


def run_exclude(args: argparse.Namespace) -> int:
    context = repo_context(args)
    local = load_local_config(context.repo_root)
    if args.rule_id in local.excluded_rule_ids:
        print(f"'{args.rule_id}' is already excluded")
    else:
        local.excluded_rule_ids.append(args.rule_id)
        save_local_config(context.repo_root, local)
        print(f"Excluded '{args.rule_id}' in .byolsp/local.yml")
    _sync_current_repo(context)
    return 0


def run_include(args: argparse.Namespace) -> int:
    context = repo_context(args)
    local = load_local_config(context.repo_root)
    if args.rule_id not in local.excluded_rule_ids:
        print(f"'{args.rule_id}' is not excluded")
    else:
        local.excluded_rule_ids.remove(args.rule_id)
        save_local_config(context.repo_root, local)
        print(f"Re-enabled '{args.rule_id}'")
    plan = _sync_current_repo(context)
    # A project or local rule may still own the ID (SPEC 15.7): say so.
    for rule_id, reason in plan.skipped:
        if rule_id == args.rule_id:
            print(f"'{rule_id}' is still skipped: {reason}")
    return 0


def _sync_current_repo(context: RepoContext) -> SyncPlan:
    """Post-action sync of the current repo, reporting only when it changed."""
    canonical = load_canonical_rules(context.config_dir)
    plan, result = sync_repo(context.repo_root, canonical)
    if result.changed:
        print(f"Synced {summarize_changes(result)} into {context.repo_root}")
    return plan
