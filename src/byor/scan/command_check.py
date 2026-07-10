"""Gate agent shell commands against command rules.

The pre-command hook fires before every shell command an agent runs, so this module is built around
two invariants: the fast path (no command rules, no command checks) exits before any subprocess, and
every failure fails open to allow — a deny is always a deliberate permission decision, never a
byor crash. Steering, not sandboxing: the deny message names the house rule and the replacement
command, and makes no claim to stop a determined evasion.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from byor.agents.harness import emit_deny, parse_command_payload
from byor.config import (
    command_rule_dir_relpaths,
    disabled_entry,
    global_commands_dir,
    load_global_config,
    load_repo_config,
    repo_config_path,
)
from byor.io.paths import global_config_dir, resolve_repo_root
from byor.rules.rules import load_rules
from byor.scan.agent_check import DIAGNOSTICS_EXIT_CODE, FAIL_OPEN_ERRORS
from byor.scan.astgrep import resolve_ast_grep, scan_command
from byor.scan.checks import CheckOutcome, load_effective_command_checks, run_command_checks

if TYPE_CHECKING:
    import argparse
    from pathlib import Path

    from byor.agents.harness import Harness
    from byor.config import GlobalConfig
    from byor.rules.rules import Rule
    from byor.scan.astgrep import ScanMatch

__all__ = (
    "load_effective_command_rules",
    "render_deny",
    "run_command_check",
)


def run_command_check(args: argparse.Namespace) -> int:
    harness: Harness | None = args.stdin_hook
    if harness is not None:
        return _run_hook(args, harness=harness)
    return _run_command_text(args)


def _run_command_text(args: argparse.Namespace) -> int:
    """Gate an explicit `--command` string: the human/test mode.

    Prints the same deny text an agent would receive and exits 2 on findings,
    so rule authors can verify a freshly captured command rule fires (and that
    innocent commands pass) without wiring up a harness payload.
    """
    config_dir = global_config_dir()
    global_config = load_global_config(config_dir)
    repo_root = resolve_repo_root(explicit=args.repo)
    rendered = _gate(repo_root, args.command_text, global_config=global_config, config_dir=config_dir)
    if not rendered:
        return 0
    sys.stdout.write(f"{rendered}\n")
    return DIAGNOSTICS_EXIT_CODE


def _run_hook(args: argparse.Namespace, *, harness: Harness) -> int:
    # fail-open: a byor bug must never block the agent's command
    try:
        return _hook_gate(args, harness=harness)
    except FAIL_OPEN_ERRORS as error:
        # Exit 0 approves; the breadcrumb keeps "byor couldn't run" separable
        # from "byor allowed it". `byor doctor` surfaces the root cause.
        sys.stderr.write(f"byor: command-check skipped after an internal error: {error}\n")
        return 0


def _hook_gate(args: argparse.Namespace, *, harness: Harness) -> int:
    """Parse the payload, gate the command, and emit the permission decision.

    The repo is resolved from the payload's cwd — a pre-command hook references
    no file — falling back to the process cwd. A disabled repo, a payload with
    no command (another tool's hook fired), and a repo with neither command
    rules nor command checks all approve silently without shelling out.
    """
    config_dir = global_config_dir()
    global_config = load_global_config(config_dir)
    payload = parse_command_payload(harness, sys.stdin.read())
    if payload.command is None:
        return 0
    repo_root = resolve_repo_root(explicit=args.repo, start=payload.cwd)
    if disabled_entry(repo_root, global_config) is not None:
        return 0
    rendered = _gate(repo_root, payload.command, global_config=global_config, config_dir=config_dir)
    stdout, exit_code = emit_deny(harness, rendered)
    if stdout:
        sys.stdout.write(f"{stdout}\n")
    return exit_code


def _gate(repo_root: Path, command: str, *, global_config: GlobalConfig, config_dir: Path) -> str:
    rules = load_effective_command_rules(repo_root, config_dir=config_dir, global_config=global_config)
    checks = load_effective_command_checks(repo_root, config_dir)
    if not rules and not checks:
        # The every-shell-command fast path: nothing to gate, no subprocess.
        return ""
    matches: list[ScanMatch] = []
    if rules:
        executable = resolve_ast_grep(global_config.ast_grep_command)
        result = scan_command(executable, command, rules=[rule.content for rule in rules])
        matches = result.matches
        if result.warnings:
            sys.stderr.write(f"{result.warnings}\n")
    outcome = run_command_checks(checks, repo_root, command=command)
    for warning in outcome.warnings:
        sys.stderr.write(f"{warning}\n")
    return render_deny(matches, outcome, limit=global_config.output_max_diagnostics)


def load_effective_command_rules(repo_root: Path, *, config_dir: Path, global_config: GlobalConfig) -> list[Rule]:
    """Load the Bash command rules that govern this repository.

    An initialized repo reads its four `.byor/commands` scope directories —
    sync already applied precedence and exclusions when it wrote the mirrors.
    Any other repo falls back to the canonical global command rules directly,
    mirroring how file rules fall back to the home sgconfig. Rules in another
    language are skipped: the command string is parsed as Bash.
    """
    if repo_config_path(repo_root).is_file():
        paths = load_repo_config(repo_root).paths
        dirs = [repo_root / relpath for relpath in command_rule_dir_relpaths(paths)]
    else:
        dirs = [global_commands_dir(config_dir, global_config)]
    rules = [rule for rules_dir in dirs for rule in load_rules(rules_dir)]
    return [rule for rule in rules if rule.language.lower() == "bash"]


def render_deny(matches: list[ScanMatch], outcome: CheckOutcome, *, limit: int | None) -> str:
    """Render the corrective deny message, or "" to approve.

    Any match denies: command rules are opt-in and authored to steer, so there
    is no severity threshold. The message stays concise — it is an instruction
    the agent acts on, not a report — and always ends by asking for a rewrite.
    """
    if not matches and not outcome.failures:
        return ""
    total = len(matches) + len(outcome.failures)
    noun = "house rule" if total == 1 else "house rules"
    lines = [f"BYOR blocked this command — it breaks {total} {noun} (byor steering, not a permissions failure)."]
    shown = matches if limit is None else matches[:limit]
    for match in shown:
        lines += ["", f"[{match.severity}] {match.rule_id}: {match.message}"]
        instruction = (match.agent_prompt or "").strip()
        if instruction:
            lines.append(f"Do this instead: {instruction}")
    hidden = len(matches) - len(shown)
    if hidden:
        lines += ["", f"... and {hidden} more not shown (raise output.max_diagnostics)."]
    for failure in outcome.failures:
        lines += ["", failure]
    lines += ["", "Rewrite the command as instructed and run it again."]
    return "\n".join(lines)
