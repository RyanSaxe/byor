"""Self-heal machine and repository state before commands run.

The CLI preamble calls these to keep global agent integrations and repo mirrors converged with the
installed byor. Healing degrades to warnings instead of failing, so a broken agent config or repo
never blocks the unrelated command — often the very command that fixes it.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from byor.agents.install import install_agent
from byor.config import global_rules_dir, load_global_config, load_machine_state, repo_config_path
from byor.errors import ByorError, ConfigError
from byor.rules.sync import load_canonical_rules, summarize_changes, sync_repo
from byor.scaffold.ignore import refresh_ignore_blocks
from byor.scaffold.sgconfig import ensure_home_sgconfig
from byor.scaffold.worktree import adopt_main_checkout

if TYPE_CHECKING:
    from pathlib import Path

__all__ = (
    "heal_global",
    "heal_ignore_blocks",
    "heal_repo",
    "heal_worktree",
)


def heal_global(config_dir: Path, state_dir: Path) -> list[str]:
    """Keep machine-level state current with the installed byor, quietly.

    Runs on every command (even outside a repo): refreshes the global skill
    render so a byor upgrade is reflected without a reinstall, and reconverges
    recorded agent hooks/plugins plus `~/sgconfig.yml` so upgrades keep applying
    everywhere. A broken agent config must not fail unrelated commands, so each
    agent heals independently; the returned warnings name the agents skipped.
    """
    config = load_global_config(config_dir)
    rules_dir = global_rules_dir(config_dir, config)
    warnings: list[str] = []
    if rules_dir.is_dir():
        # A malformed ~/sgconfig.yml must degrade like a broken agent config:
        # crashing here would block every self-healing command.
        try:
            ensure_home_sgconfig(rules_dir)
        except ConfigError as error:
            warnings.append(f"byor: skipping ~/sgconfig.yml self-heal: {error} (run 'byor doctor')")
    for agent in load_machine_state(state_dir).agents:
        try:
            install_agent(agent)
        except ConfigError as error:
            warnings.append(f"byor: skipping {agent} self-heal: {error} (run 'byor doctor')")
    return warnings


def heal_repo(repo_root: Path, config_dir: Path) -> str | None:
    """Resync the repo mirrors quietly before a command runs.

    Like heal_global, a broken repo must not fail unrelated commands: a sync
    error (say, two installed packages colliding on a rule ID) degrades to a
    warning so the command that fixes it — `byor exclude` — can still run.
    """
    if not repo_config_path(repo_root).is_file():
        return None
    try:
        _, result = sync_repo(repo_root, load_canonical_rules(config_dir))
    except ByorError as error:
        return f"byor: skipping repo self-heal: {error} (run 'byor doctor')"
    if not result.changed:
        return None
    return f"byor: synced {summarize_changes(result)}"


def heal_ignore_blocks(repo_root: Path) -> list[str]:
    """Rewrite the repo's outdated byor ignore blocks, one line per file changed.

    The preamble runs this after `heal_repo`, so a repo initialized before byor gained a personal
    path stops offering the new copies to git on its next command. A repo without
    `.byor/config.yml` is skipped, since only `byor init` chooses where a block goes. Like the
    other heals, a git or file error degrades to a warning so the command still runs, and a repo
    whose blocks are current returns no lines.
    """
    if not repo_config_path(repo_root).is_file():
        return []
    try:
        changed = refresh_ignore_blocks(repo_root)
    except (ConfigError, OSError) as error:
        return [f"byor: skipping ignore block self-heal: {error} (run 'byor doctor')"]
    return [f"byor: updated the byor ignore block in {os.path.relpath(path, repo_root)}" for path in changed]


def heal_worktree(repo_root: Path, config_dir: Path) -> str | None:
    """Give a new linked worktree its main checkout's byor files, quietly.

    Runs before `heal_repo` and at the start of both agent hooks, so the first command or edit in
    a fresh worktree already sees the repo's rules. A failed copy degrades to a warning, like the
    other heals, and the command or hook goes on with whatever the worktree has.
    """
    try:
        return adopt_main_checkout(repo_root, config_dir)
    except (ByorError, OSError) as error:
        return f"byor: skipping worktree self-heal: {error} (run 'byor init' in this worktree)"
