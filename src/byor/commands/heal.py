"""Self-heal machine and repository state before commands run.

The CLI preamble calls these to keep global agent integrations and repo mirrors converged with the
installed byor. Healing degrades to warnings instead of failing, so a broken agent config or repo
never blocks the unrelated command — often the very command that fixes it.
"""

from __future__ import annotations

import os
from pathlib import Path

from byor.agents.install import AGENT_CHOICES, install_agent, unrecorded_agents
from byor.config import (
    global_rules_dir,
    load_current_machine_state,
    load_global_config,
    load_machine_state,
    repo_config_path,
    save_machine_state,
)
from byor.errors import ByorError, ConfigError, WorktreeCopyBusyError
from byor.io.paths import global_state_dir
from byor.rules.sync import load_canonical_rules, summarize_changes, sync_repo
from byor.scaffold.ignore import refresh_ignore_blocks
from byor.scaffold.sgconfig import ensure_home_sgconfig
from byor.scaffold.worktree import copy_from_main_checkout, pending_copy, worktree_disabled_entry

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
    everywhere. An agent whose byor hooks or plugin file are installed but
    unrecorded gets recorded first, so its integration is reconverged too. A
    broken agent config must not fail unrelated commands, so each agent heals
    independently; the returned lines name what was recorded and what was skipped.
    """
    config = load_global_config(config_dir)
    rules_dir = global_rules_dir(config_dir, config)
    messages: list[str] = []
    if rules_dir.is_dir():
        # A malformed ~/sgconfig.yml must degrade like a broken agent config:
        # crashing here would block every self-healing command.
        try:
            ensure_home_sgconfig(rules_dir)
        except ConfigError as error:
            messages.append(f"byor: skipping ~/sgconfig.yml self-heal: {error} (run 'byor doctor')")
    messages.extend(_drop_unsupported_agents(state_dir))
    messages.extend(_record_unrecorded_agents(state_dir))
    for agent in load_machine_state(state_dir).agents:
        try:
            install_agent(agent)
        except ConfigError as error:
            messages.append(f"byor: skipping {agent} self-heal: {error} (run 'byor doctor')")
    return messages


# A name byor does not support, such as one an old byor or a hand edit left in
# the list, can never be installed, so keeping it would only show it as healthy.
def _drop_unsupported_agents(state_dir: Path) -> list[str]:
    state = load_machine_state(state_dir)
    unsupported = [agent for agent in state.agents if agent not in AGENT_CHOICES]
    if not unsupported:
        return []
    state.agents = [agent for agent in state.agents if agent in AGENT_CHOICES]
    try:
        save_machine_state(state_dir, state)
    except ConfigError as error:
        return [f"byor: skipping agent cleanup: {error}"]
    return [f"byor: dropped agents byor does not support from state.yml: {', '.join(unsupported)}"]


def _record_unrecorded_agents(state_dir: Path) -> list[str]:
    scan = unrecorded_agents(load_machine_state(state_dir).agents)
    messages = [f"byor: skipping agent detection: {problem} (run 'byor doctor')" for problem in scan.problems]
    if not scan.found:
        return messages
    # Re-read just before writing, so a disable or enable that another byor
    # command saved while detection ran is kept rather than overwritten.
    state = load_machine_state(state_dir)
    state.agents.extend(agent for agent in scan.found if agent not in state.agents)
    try:
        save_machine_state(state_dir, state)
    except ConfigError as error:
        return [*messages, f"byor: skipping agent recording: {error}"]
    return [*messages, f"byor: recorded {', '.join(scan.found)}, whose byor integration was already installed"]


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
    `.byor/config.yml` is skipped, since only `byor init` chooses where a block goes. A file byor
    cannot write is skipped without a line, because repeating it on every command would not help;
    `byor doctor` and `byor sync` name it. A repo whose blocks are current returns no lines.
    """
    if not repo_config_path(repo_root).is_file():
        return []
    try:
        refreshed = refresh_ignore_blocks(repo_root)
    except ConfigError as error:
        return [f"byor: skipping ignore block self-heal: {error} (run 'byor doctor')"]
    return [
        f"byor: updated the byor ignore block in {Path(os.path.relpath(path, repo_root)).as_posix()}"
        for path in refreshed.changed
    ]


def heal_worktree(repo_root: Path, config_dir: Path) -> list[str]:
    """Give a new linked worktree its main checkout's byor files, then sync its mirrors.

    The CLI runs this before every command except doctor and `byor sync --check`, and both agent
    hooks run it first, so the first command or edit in a fresh worktree already sees the repo's
    rules. A worktree or main checkout that `byor disable` covers is left alone. A failed copy
    degrades to a warning, like the other heals, and leaves no `.byor/local.yml`, so the next
    command or hook retries it. After a copy, the worktree's ignore block is brought up to date
    before the sync writes mirrors, so a branch with an older block never shows them in git status;
    a sync that then fails is reported on its own line, after the copy line. WorktreeCopyBusyError
    propagates, since commands stop on it and hooks report it.
    """
    try:
        pending = pending_copy(repo_root)
        if pending is None:
            return []
        if worktree_disabled_entry(repo_root, load_current_machine_state(config_dir, global_state_dir())) is not None:
            return []
        copied = copy_from_main_checkout(repo_root)
    except WorktreeCopyBusyError:
        raise
    except (ByorError, OSError) as error:
        return [f"byor: skipping worktree self-heal: {error} (run 'byor sync' to retry)"]
    if copied is None:
        return []
    noun = "file" if len(copied.relpaths) == 1 else "files"
    lines = [f"byor: copied {len(copied.relpaths)} byor {noun} from the main checkout {copied.main_root}"]
    lines.extend(heal_ignore_blocks(repo_root))
    try:
        sync_repo(repo_root, load_canonical_rules(config_dir))
    except ByorError as error:
        lines.append(f"byor: skipping the sync after the worktree copy: {error} (run 'byor sync' after fixing it)")
    return lines
