"""Self-heal machine and repository state before commands run.

The CLI preamble calls these to keep global agent integrations and repo mirrors converged with the
installed byor. Healing degrades to warnings instead of failing, so a broken agent config or repo
never blocks the unrelated command — often the very command that fixes it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from byor.agents.install import install_agent
from byor.config import global_rules_dir, load_global_config, repo_config_path
from byor.errors import ByorError, ConfigError
from byor.rules.sync import load_canonical_rules, summarize_changes, sync_repo
from byor.scaffold.sgconfig import ensure_home_sgconfig

if TYPE_CHECKING:
    from pathlib import Path

__all__ = (
    "heal_global",
    "heal_repo",
)


def heal_global(config_dir: Path) -> list[str]:
    """Keep machine-level state current with the installed byor, quietly.

    Runs on every command (even outside a repo): refreshes the global skill
    render so a byor upgrade is reflected without a reinstall, and reconverges
    recorded agent hooks/plugins plus `~/sgconfig.yml` so upgrades keep applying
    everywhere. A broken agent config must not fail unrelated commands, so each
    agent heals independently; the returned warnings name the agents skipped.
    """
    config = load_global_config(config_dir)
    rules_dir = global_rules_dir(config_dir, config)
    if rules_dir.is_dir():
        ensure_home_sgconfig(rules_dir)
    warnings: list[str] = []
    for agent in config.agents:
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
