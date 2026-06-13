"""AI agent adapters: instruction files and real hooks (SPEC 15.10, 16, 28.3)."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from byolsp.config import load_repo_config, save_repo_config
from byolsp.errors import ConfigError
from byolsp.fsio import MANAGED_MARKER, marked_text_status, write_marked_text
from byolsp.harness import HARNESS_CHOICES, Harness
from byolsp.hookconfig import HookScope, install_hook, uninstall_hook
from byolsp.opencode import OPENCODE_MARKER, OPENCODE_PLUGIN, OPENCODE_PLUGIN_RELPATH
from byolsp.paths import resolve_repo_root
from byolsp.rules import SUPPRESSION_COMMENT
from byolsp.skill import SKILL_MARKDOWN, SKILL_RELPATHS

# The four real-hook harnesses plus the OpenCode plugin and the bare adapters.
HOOK_HARNESSES: frozenset[str] = frozenset(HARNESS_CHOICES)

AGENT_CHOICES = (
    "generic",
    "claude-code",
    "codex",
    "copilot",
    "cursor",
    "opencode",
    "skill",
)

AGENT_INSTRUCTIONS_RELPATH = ".byolsp/agents/README.md"

CORE_INSTRUCTION = f"""\
This repository uses BYOLSP to expose custom ast-grep diagnostics.

After writing or editing code, run:

```bash
byolsp agent-check --scope diff --files <changed files>
```

If BYOLSP reports a diagnostic, fix it before continuing.

If a rule's instruction permits exceptions, only keep the violating code when
genuinely necessary, and suppress it with
`{SUPPRESSION_COMMENT}` on its own line above the violation.
"""

GENERIC_AGENT_INSTRUCTIONS = (
    f"{MANAGED_MARKER}\n\n# BYOLSP Agent Instructions\n\n{CORE_INSTRUCTION}"
)

# SPEC 27.4: every harness that auto-discovers skills gets the capture loop.
SKILL_DISCOVERY_NOTE = (
    "{harness} also auto-discovers the `byolsp` rule-capture skill at\n"
    "`.agents/skills/byolsp/SKILL.md`; use it to turn the user's durable\n"
    "code-style feedback into new ast-grep rules."
)


def run_hook(args: argparse.Namespace) -> int:
    """`byolsp hook install|uninstall --agent NAME [--hook-scope SCOPE]` (SPEC 28.3).

    Installed agents are recorded in ai.agents so doctor and uninstall know
    about them (SPEC 10.1).
    """
    repo_root = resolve_repo_root(explicit=args.repo)
    config = load_repo_config(repo_root)
    if args.hook_action == "install":
        if args.hook_scope == "local" and args.agent != "claude-code":
            raise ConfigError("--hook-scope local is only supported for claude-code")
        messages = install_agent(repo_root, args.agent, args.hook_scope)
        recorded = args.agent not in config.agents
        if recorded:
            config.agents.append(args.agent)
    else:
        messages = uninstall_agent(repo_root, args.agent)
        recorded = args.agent in config.agents
        if recorded:
            config.agents.remove(args.agent)
    if recorded:
        save_repo_config(repo_root, config)
    for message in messages:
        print(message)
    return 0


def install_agents(
    repo_root: Path, agents: Sequence[str], hook_scope: HookScope = "project"
) -> list[str]:
    """Init step 5: the generic README is part of the repository layout (SPEC 6);
    the explicitly requested agents get their adapters on top.
    """
    messages = install_agent(repo_root, "generic", hook_scope)
    for agent in agents:
        if agent != "generic":
            messages.extend(install_agent(repo_root, agent, hook_scope))
    return messages


def install_agent(
    repo_root: Path, agent: str, hook_scope: HookScope = "project"
) -> list[str]:
    """Install one agent adapter; returns summary lines for changes made."""
    if agent == "skill":
        return _install_skill(repo_root)
    messages: list[str] = []
    if agent == "opencode":
        # A real post-edit plugin on top of the instruction file (SPEC 27.3).
        messages.extend(
            _write_managed_file(
                repo_root,
                OPENCODE_PLUGIN_RELPATH,
                OPENCODE_PLUGIN,
                marker=OPENCODE_MARKER,
            )
        )
    harness = _as_harness(agent)
    if harness is not None:
        messages.extend(install_hook(repo_root, harness, hook_scope))
    messages.extend(
        _write_managed_file(
            repo_root, _instructions_relpath(agent), _agent_instructions(agent)
        )
    )
    return messages


def uninstall_agent(repo_root: Path, agent: str) -> list[str]:
    """Remove one agent adapter; only marker-bearing files are deleted (SPEC 17)."""
    messages: list[str] = []
    if agent == "skill":
        for relpath in SKILL_RELPATHS:
            messages.extend(_remove_managed_file(repo_root, relpath))
        return messages
    harness = _as_harness(agent)
    if harness is not None:
        for scope in _agent_hook_scopes(agent):
            messages.extend(uninstall_hook(repo_root, harness, scope))
    if agent == "opencode":
        messages.extend(
            _remove_managed_file(
                repo_root, OPENCODE_PLUGIN_RELPATH, marker=OPENCODE_MARKER
            )
        )
    messages.extend(_remove_managed_file(repo_root, _instructions_relpath(agent)))
    return messages


def agent_file_problems(repo_root: Path, agents: Sequence[str]) -> list[str]:
    """Human-readable integration-file problems for doctor's agent_files check."""
    problems: list[str] = []
    for agent in agents:
        if agent == "skill":
            problems.extend(_skill_render_problems(repo_root))
            continue
        if agent == "opencode":
            problems.extend(_opencode_plugin_problems(repo_root))
        relpath = _instructions_relpath(agent)
        if not (repo_root / relpath).is_file():
            problems.append(f"{relpath} is missing")
    return problems


def _as_harness(agent: str) -> Harness | None:
    """The Harness for an agent that drives a real hook, else None."""
    for harness in HARNESS_CHOICES:
        if harness == agent:
            return harness
    return None


def _agent_hook_scopes(agent: str) -> tuple[HookScope, ...]:
    """Scopes a harness may have written hooks to; uninstall sweeps all of them."""
    if agent == "claude-code":
        return ("project", "global", "local")
    return ("project", "global")


def _install_skill(repo_root: Path) -> list[str]:
    """Render the rule-capture skill into both discovery locations (SPEC 27.1)."""
    messages: list[str] = []
    for relpath in SKILL_RELPATHS:
        messages.extend(_write_managed_file(repo_root, relpath, SKILL_MARKDOWN))
    return messages


def _opencode_plugin_problems(repo_root: Path) -> list[str]:
    """Same ownership rules as the skill renders: drifted marker-bearing
    plugins need a reinstall; unmarked files are user-owned and accepted.
    """
    status = marked_text_status(
        repo_root / OPENCODE_PLUGIN_RELPATH, OPENCODE_PLUGIN, OPENCODE_MARKER
    )
    if status == "missing":
        return [f"{OPENCODE_PLUGIN_RELPATH} is missing"]
    if status == "drifted":
        return [f"{OPENCODE_PLUGIN_RELPATH} is out of date"]
    return []


def _skill_render_problems(repo_root: Path) -> list[str]:
    """Both renders must exist and match the canonical content (SPEC 27.2).

    A marker-bearing render that drifted from the canonical content counts:
    `byolsp hook install --agent skill` refreshes it. Unmarked files at these
    paths are user-owned and accepted as is.
    """
    problems: list[str] = []
    for relpath in SKILL_RELPATHS:
        status = marked_text_status(repo_root / relpath, SKILL_MARKDOWN, MANAGED_MARKER)
        if status == "missing":
            problems.append(f"{relpath} is missing")
        elif status == "drifted":
            problems.append(f"{relpath} is out of date")
    return problems


def _instructions_relpath(agent: str) -> str:
    if agent == "generic":
        return AGENT_INSTRUCTIONS_RELPATH
    return f".byolsp/agents/{agent}.md"


# Display name and wiring note per instruction-file agent; _agent_instructions
# appends SKILL_DISCOVERY_NOTE to every entry, so notes stay pure wiring text.
INSTRUCTION_AGENT_NOTES = {
    "claude-code": (
        "Claude Code",
        "`byolsp hook install --agent claude-code` registers a real\n"
        "PostToolUse hook that runs this check automatically; the command\n"
        "above is the manual fallback for files changed another way.",
    ),
    "codex": (
        "Codex",
        "`byolsp hook install --agent codex` registers a real PostToolUse\n"
        "hook (trust it via `/hooks`); Codex also reads repository guidance\n"
        "from `AGENTS.md`, so copy the instruction above there too.",
    ),
    "copilot": (
        "Copilot",
        "`byolsp hook install --agent copilot` registers a real postToolUse\n"
        "hook; GitHub Copilot also reads `.github/copilot-instructions.md`,\n"
        "so copy the instruction above there too.",
    ),
    "cursor": (
        "Cursor",
        "`byolsp hook install --agent cursor` registers a real postToolUse\n"
        "hook in `.cursor/hooks.json`; the command above is the manual\n"
        "fallback for files changed another way.",
    ),
    "opencode": (
        "OpenCode",
        "The BYOLSP plugin at\n"
        f"`{OPENCODE_PLUGIN_RELPATH}` hooks `tool.execute.after` and appends\n"
        "diagnostics automatically when an `edit`, `write`, or `apply_patch`\n"
        "call names a single `filePath` — do not rerun `agent-check` for\n"
        "those. Run the command above for files changed another way (a\n"
        "multi-file `apply_patch`, or shell commands).",
    ),
}


def _agent_instructions(agent: str) -> str:
    if agent == "generic":
        return GENERIC_AGENT_INSTRUCTIONS
    name, wiring_note = INSTRUCTION_AGENT_NOTES[agent]
    return _instruction_file(
        f"BYOLSP {name} Instructions",
        f"{wiring_note}\n\n{SKILL_DISCOVERY_NOTE.format(harness=name)}",
    )


def _instruction_file(title: str, wiring_note: str) -> str:
    return f"{MANAGED_MARKER}\n\n# {title}\n\n{CORE_INSTRUCTION}\n{wiring_note}\n"


def _write_managed_file(
    repo_root: Path, relpath: str, content: str, marker: str = MANAGED_MARKER
) -> list[str]:
    result = write_marked_text(repo_root / relpath, content, marker)
    if result == "unmarked":
        return [f"{relpath} exists without the BYOLSP marker; left untouched."]
    if result == "unchanged":
        return []
    return [f"Wrote {relpath}"]


def _remove_managed_file(
    repo_root: Path, relpath: str, marker: str = MANAGED_MARKER
) -> list[str]:
    path = repo_root / relpath
    if not path.is_file():
        return []
    if marker not in path.read_text(encoding="utf-8"):
        return [f"{relpath} exists without the BYOLSP marker; left untouched."]
    path.unlink()
    return [f"Removed {relpath}"]
