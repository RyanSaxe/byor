"""Run `byor sync`: bring one repository, or every registered one, up to date.

The command mirrors rules through the sync engine, refreshes byor's ignore blocks, and with
`--check` only reports what a sync would change. `--all` fans out across registered repositories
and their worktrees.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from byor.commands.gate import regenerate_gate, stale_gate_files
from byor.config import (
    load_global_config,
    load_repo_config,
    load_repo_registry,
    locked_state_file,
    repo_registry_path,
    save_repo_registry,
)
from byor.errors import ByorError
from byor.io.paths import global_config_dir, global_state_dir, resolve_repo_root
from byor.rules.sync import CanonicalRules, iter_fan_out_repos, load_canonical_rules, repo_is_stale, sync_repo
from byor.scaffold.ignore import outdated_ignore_blocks, refresh_ignore_blocks
from byor.scaffold.worktree import pending_copy

if TYPE_CHECKING:
    import argparse

__all__ = (
    "STALE_EXIT_CODE",
    "run_sync",
)

STALE_EXIT_CODE = 3


def run_sync(args: argparse.Namespace) -> int:
    config_dir = global_config_dir()
    if args.prune:
        if not args.all:
            msg = "--prune requires --all"
            raise ByorError(msg)
        if args.check:
            msg = "--prune cannot be combined with --check"
            raise ByorError(msg)
        _prune_registry(config_dir)
    canonical = load_canonical_rules(config_dir)
    if args.all:
        return _sync_all(config_dir, canonical, check=args.check)
    repo_root = resolve_repo_root(explicit=args.repo)
    if args.check:
        return _report_staleness(repo_root, canonical)
    _sync_and_report(repo_root, canonical)
    return 0


def _prune_registry(config_dir: Path) -> None:
    """Drop registry entries whose paths no longer exist on disk.

    Only nonexistent paths are pruned: existing repos keep their registration,
    disabled-but-existing ones included, so pruning can never forget a repo the
    user still has. The registry lock keeps a repo that a concurrent init registers.
    """
    registry_path = repo_registry_path(global_state_dir(), load_global_config(config_dir))
    with locked_state_file(registry_path):
        repos = load_repo_registry(registry_path)
        removed = [repo for repo in repos if not repo.is_dir()]
        if removed:
            save_repo_registry(registry_path, [repo for repo in repos if repo.is_dir()])
    for repo in removed:
        sys.stdout.write(f"Pruned {repo} from the registry\n")


def _sync_all(config_dir: Path, canonical: CanonicalRules, *, check: bool) -> int:
    exit_code = 0
    for repo_root in iter_fan_out_repos(config_dir):
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
    # sync_repo raises for a repo without .byor/config.yml, so the ignore block of an
    # uninitialized repo is never touched, and a block that cannot be rewritten never stops
    # the rules from syncing.
    plan, result = sync_repo(repo_root, canonical)
    refreshed = refresh_ignore_blocks(repo_root)
    # A steady-state sync (the post-merge/post-checkout shims run one on every
    # pull) must not narrate forever: silence is the unix success signal, so a
    # sync that changed nothing prints nothing; `byor list` keeps skips
    # visible on demand.
    if result.changed:
        # Count each kind of copy the repo now has, so a sync driven by a package rule never reads
        # as "Synced 0 global rules"; a repo with no copies at all keeps the plain zero count.
        synced = [_count(kind.desired, kind.noun) for kind in result.kinds if kind.desired] or [
            _count(0, "global rule")
        ]
        sys.stdout.write(f"Synced {', '.join(synced)} into {repo_root}\n")
        if plan.skipped:
            sys.stdout.write(f"Skipped {_count(len(plan.skipped), 'rule')}:\n")
            for skipped in plan.skipped:
                sys.stdout.write(f"  {skipped.id}: {skipped.reason}\n")
    # Full paths, because `sync --all` reports many repos in one stream.
    for path in refreshed.changed:
        sys.stdout.write(f"Updated the byor ignore block in {path}\n")
    for path, reason in refreshed.unwritable:
        sys.stderr.write(
            f"byor: cannot update the out-of-date byor ignore block in {path}: {reason}; "
            "make the file writable, then run `byor sync`\n"
        )
    if load_repo_config(repo_root).gate:
        for message in regenerate_gate(repo_root):
            sys.stdout.write(f"{message}\n")


def _report_staleness(repo_root: Path, canonical: CanonicalRules) -> int:
    pending = pending_copy(repo_root)
    if pending is not None and pending.byor_relpaths:
        sys.stdout.write(
            f"Sync is stale in {repo_root}; run `byor sync` to copy byor's files from the main checkout.\n"
        )
        return STALE_EXIT_CODE
    outdated = outdated_ignore_blocks(repo_root)
    if unwritable := [status for status in outdated if not status.writable]:
        listed = ", ".join(Path(os.path.relpath(status.path, repo_root)).as_posix() for status in unwritable)
        sys.stdout.write(
            f"Sync is stale in {repo_root}: the byor ignore block in {listed} is out of date and the file is"
            " not writable; make it writable, then run `byor sync`.\n"
        )
        return STALE_EXIT_CODE
    repo_config = load_repo_config(repo_root)
    stale_gate = repo_config.gate and bool(stale_gate_files(repo_root, repo_config))
    if repo_is_stale(repo_root, canonical) or outdated or stale_gate:
        sys.stdout.write(f"Sync is stale in {repo_root}; run `byor sync`.\n")
        return STALE_EXIT_CODE
    sys.stdout.write(f"Sync is fresh in {repo_root}\n")
    return 0


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}{'' if number == 1 else 's'}"
