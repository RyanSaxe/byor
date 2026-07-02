"""Build and dispatch the BYOR command-line interface.

The CLI module owns argparse construction, pre-command self-healing, and command dispatch while
implementation details live in focused command modules. That separation keeps the command surface
auditable without mixing it with rule or scan logic.
"""

from __future__ import annotations

import argparse
import io
import sys
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING

from byor import __version__
from byor.agents.harness import HARNESS_CHOICES
from byor.agents.install import AGENT_CHOICES, run_hook
from byor.commands.disable import run_disable, run_enable
from byor.commands.doctor import run_doctor
from byor.commands.gate import heal_gate
from byor.commands.heal import heal_global, heal_repo
from byor.commands.init import run_init
from byor.commands.install import run_install
from byor.commands.listing import run_list
from byor.commands.package import run_package
from byor.commands.profile import run_profile
from byor.commands.rules import (
    run_add,
    run_edit,
    run_exclusion,
    run_promote,
    run_remove,
)
from byor.config import disabled_entry, load_global_config
from byor.errors import ByorError
from byor.io.paths import global_config_dir, resolve_repo_root
from byor.rules.sync import run_sync
from byor.scan.agent_check import run_agent_check

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

__all__ = (
    "build_parser",
    "main",
    "run",
)

COMMANDS = {
    "install": "Register byor's AI integrations globally (one-time)",
    "init": "Initialize BYOR in a repository",
    "sync": "Mirror enabled global rules into the repository",
    "doctor": "Validate installation health",
    "add": "Create a new rule in a scope",
    "edit": "Open an existing rule in $EDITOR",
    "remove": "Delete a rule from its scope",
    "promote": "Move a personal rule into shared project rules",
    "exclude": "Disable a global rule in this repository",
    "include": "Re-enable a previously excluded global rule",
    "disable": "Turn byor off for a repository or a directory of repositories",
    "enable": "Turn byor back on for a disabled path",
    "profile": "List and apply local exclusion profiles",
    "package": "List and install opt-in rule/check packages",
    "list": "Show rules and where they come from",
    "agent-check": "Run ast-grep on changed files and render agent feedback",
    "hook": "Install or uninstall AI agent integrations",
}

REPO_COMMANDS = frozenset(COMMANDS) - {"install", "hook", "profile", "package", "disable", "enable"}
REPO_HELP = "Repository root (default: search upward from cwd)"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="byor",
        description="Custom ast-grep diagnostics, easy to set up, share, and expose to AI agents.",
    )
    parser.add_argument("--version", action="version", version=f"byor {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name, help_text in COMMANDS.items():
        command = subparsers.add_parser(name, help=help_text, description=help_text)
        # Commands that have not yet grown a --repo flag still get args.repo.
        command.set_defaults(repo=None)
        if name in REPO_COMMANDS:
            command.add_argument("--repo", type=Path, help=REPO_HELP)
        _COMMAND_ARGUMENTS[name](command)
    return parser


def _add_install_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "--agents",
        help=f"Comma-separated AI integrations: {', '.join(AGENT_CHOICES)}",
    )
    command.add_argument(
        "--non-interactive",
        action="store_true",
        help="Use the recorded agents instead of prompting",
    )


def _add_init_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "--private",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Hide byor's whole footprint via .git/info/exclude; commit nothing",
    )
    command.add_argument(
        "--git-hooks",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Install post-merge/post-checkout shims that run `byor sync`",
    )
    command.add_argument(
        "--gate",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Distribute a blocking gate (pre-commit + CI) enforcing these rules",
    )
    command.add_argument(
        "--non-interactive",
        action="store_true",
        help="Use defaults instead of prompting",
    )
    command.add_argument(
        "--no-register",
        action="store_true",
        help="Skip registering this repository for `byor sync --all`",
    )
    command.add_argument(
        "--replace-sgconfig",
        action="store_true",
        help="Overwrite sgconfig.yml after saving a timestamped backup",
    )
    profile = command.add_mutually_exclusive_group()
    profile.add_argument(
        "--profile",
        help="Apply a configured profile before the initial sync",
    )
    profile.add_argument(
        "--no-profile",
        action="store_true",
        help="Skip applying init.profile from global config",
    )


def _add_sync_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument("--all", action="store_true", help="Sync every registered repository")
    command.add_argument(
        "--check",
        action="store_true",
        help="Report staleness without writing; exit 3 when stale",
    )


def _add_list_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "--scope",
        # Spelled out so --help stays import-light; keep in sync with listing.ListScope.
        choices=("project", "local", "global", "package", "effective", "all"),
        default="effective",
        help="Which rules to show (default: effective, what ast-grep sees)",
    )
    command.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    command.add_argument(
        "--tags",
        action="store_true",
        help="List discovered rule and check tags instead of rules",
    )
    command.add_argument("--tag", help="Only show rules and checks with this tag")


def _add_add_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "--scope",
        choices=("project", "local", "global"),
        required=True,
        help="Where the new rule lives",
    )
    command.add_argument("--language", help="Language for the generated template (default: Python)")
    command.add_argument("--id", help="Rule ID for the generated template")
    command.add_argument(
        "--allow-exceptions",
        action="store_true",
        help="End the rule's agent_prompt with the standard suppression sentence",
    )
    source = command.add_mutually_exclusive_group()
    source.add_argument(
        "--from",
        dest="from_file",
        type=Path,
        metavar="FILE",
        help="Copy an existing ast-grep YAML rule file",
    )
    source.add_argument(
        "--edit",
        action="store_true",
        help="Open a generated template in $EDITOR",
    )


def _add_rule_lookup_arguments(command: argparse.ArgumentParser, action: str) -> None:
    command.add_argument("rule_id", metavar="RULE_ID", help=f"ID of the rule to {action}")
    command.add_argument(
        "--scope",
        choices=("project", "local", "global", "auto"),
        default="auto",
        help="Where to look for the rule (default: project, then local, then global)",
    )


def _add_promote_arguments(command: argparse.ArgumentParser) -> None:
    target = command.add_mutually_exclusive_group(required=True)
    target.add_argument("rule_id", nargs="?", metavar="RULE_ID", help="ID of the rule to promote")
    target.add_argument(
        "--check",
        metavar="NAME",
        help="Promote a global or package check into tracked .byor/config.yml",
    )
    command.add_argument(
        "--from",
        dest="from_scope",
        choices=("local", "global", "package"),
        help="Scope the rule currently lives in (required when promoting a rule)",
    )
    command.add_argument(
        "--to",
        choices=("project",),
        default="project",
        help="Destination scope (only project is supported)",
    )
    command.add_argument(
        "--keep-local",
        action="store_true",
        help="Keep the local original when promoting from local",
    )
    command.add_argument(
        "--replace",
        action="store_true",
        help="Overwrite an existing project rule file at the destination",
    )


def _add_target_path_argument(command: argparse.ArgumentParser, *, verb: str) -> None:
    help_text = (
        f"Directory to {verb}: a repository root or a prefix covering many repositories"
        " (default: the enclosing repo root, or the cwd itself outside git)"
    )
    command.add_argument("path", nargs="?", type=Path, metavar="PATH", help=help_text)


def _add_exclusion_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "rule_id",
        nargs="?",
        metavar="RULE_ID",
        help="ID of a global rule",
    )
    command.add_argument("--tag", help="Global rule tag to exclude or include")
    command.add_argument("--check", help="Extra check name to exclude or include")
    command.add_argument("--check-tag", help="Extra check tag to exclude or include")


def _add_profile_arguments(command: argparse.ArgumentParser) -> None:
    actions = command.add_subparsers(dest="profile_action", required=True)
    actions.add_parser("list", help="List configured profiles")
    add = actions.add_parser("add", help="Add a profile's exclusions to this repository")
    add.add_argument("name", metavar="NAME", help="Profile name")
    add.add_argument("--repo", type=Path, help=REPO_HELP)


def _add_package_arguments(command: argparse.ArgumentParser) -> None:
    actions = command.add_subparsers(dest="package_action", required=True)
    actions.add_parser("list", help="List available packages")
    add = actions.add_parser("add", help="Install a package's rules and checks into this repository")
    add.add_argument("name", metavar="NAME", help="Package name")
    add.add_argument("--repo", type=Path, help=REPO_HELP)


def _add_agent_check_arguments(command: argparse.ArgumentParser) -> None:
    source = command.add_mutually_exclusive_group()
    source.add_argument(
        "--files",
        nargs="+",
        type=Path,
        default=(),
        metavar="FILE",
        help="Files to scan (default: the whole repository)",
    )
    source.add_argument(
        "--stdin-hook",
        choices=HARNESS_CHOICES,
        metavar="HARNESS",
        help=(
            "Read the edited file from a harness hook JSON payload on stdin"
            f" ({'|'.join(HARNESS_CHOICES)}) and reply in its feedback format"
        ),
    )
    command.add_argument(
        "--scope",
        choices=("edit", "diff", "file"),
        help=(
            "Keep only diagnostics on edited lines, uncommitted git diff"
            " lines, or anywhere in the scanned files (default: per mode)"
        ),
    )
    command.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output format (default: text)",
    )
    command.add_argument(
        "--concise",
        action="store_true",
        help="Trim each diagnostic to its location and fix (or set output.concise)",
    )


def _add_doctor_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "--quick",
        action="store_true",
        help="Skip rule validation and the gate, vendored-script, and git-shim staleness checks",
    )
    command.add_argument("--json", action="store_true", help="Emit machine-readable JSON")


def _add_hook_arguments(command: argparse.ArgumentParser) -> None:
    actions = command.add_subparsers(dest="hook_action", required=True)
    for action_name, action_help in (
        ("install", "Install agent integration files"),
        ("uninstall", "Remove BYOR-managed agent files"),
    ):
        action = actions.add_parser(action_name, help=action_help)
        action.add_argument(
            "--agent",
            choices=AGENT_CHOICES,
            required=True,
            help="Which AI integration to manage",
        )


_COMMAND_ARGUMENTS: dict[str, Callable[[argparse.ArgumentParser], None]] = {
    "install": _add_install_arguments,
    "init": _add_init_arguments,
    "sync": _add_sync_arguments,
    "doctor": _add_doctor_arguments,
    "add": _add_add_arguments,
    "edit": partial(_add_rule_lookup_arguments, action="edit"),
    "remove": partial(_add_rule_lookup_arguments, action="remove"),
    "promote": _add_promote_arguments,
    "exclude": _add_exclusion_arguments,
    "include": _add_exclusion_arguments,
    "disable": partial(_add_target_path_argument, verb="disable"),
    "enable": partial(_add_target_path_argument, verb="re-enable"),
    "profile": _add_profile_arguments,
    "package": _add_package_arguments,
    "list": _add_list_arguments,
    "agent-check": _add_agent_check_arguments,
    "hook": _add_hook_arguments,
}


# Commands that skip the self-heal preamble: install, init, sync, profile, and
# package perform their own healing (and `sync --check` must never write), while
# doctor is read-only diagnostics — it reports drift instead of repairing it.
# disable and enable manage the off switch itself; healing the repo about to be
# disabled would be backwards.
NO_SELF_HEAL_COMMANDS = frozenset({"install", "init", "sync", "profile", "package", "doctor", "disable", "enable"})

# Repo commands that stop with one stderr line in a disabled repo. init runs its
# own re-enable prompt and doctor is read-only reporting, so both pass through.
DISABLED_NOTICE_COMMANDS = REPO_COMMANDS - {"init", "doctor"}
DISABLED_NOTICE = "byor: this repository is disabled for byor; run `byor enable` to re-enable\n"


def _is_hook_invocation(args: argparse.Namespace) -> bool:
    return args.command == "agent-check" and getattr(args, "stdin_hook", None) is not None


_HANDLERS = {
    "install": run_install,
    "init": run_init,
    "sync": run_sync,
    "doctor": run_doctor,
    "list": run_list,
    "add": run_add,
    "edit": run_edit,
    "remove": run_remove,
    "promote": run_promote,
    "profile": run_profile,
    "package": run_package,
    "exclude": run_exclusion,
    "include": run_exclusion,
    "disable": run_disable,
    "enable": run_enable,
    "agent-check": run_agent_check,
    "hook": run_hook,
}


def run(args: argparse.Namespace) -> int:
    if _in_disabled_repo(args):
        # A human asked, so say why nothing happens; hook mode stays silent.
        sys.stderr.write(DISABLED_NOTICE)
        return 0
    if args.command not in NO_SELF_HEAL_COMMANDS and not _is_hook_invocation(args):
        for message in _self_heal_preamble(args):
            # stderr keeps stdout clean for JSON-emitting commands.
            sys.stderr.write(f"{message}\n")
    return _HANDLERS[args.command](args)


def _in_disabled_repo(args: argparse.Namespace) -> bool:
    if args.command not in DISABLED_NOTICE_COMMANDS or _is_hook_invocation(args):
        return False
    if args.command == "sync" and args.all:
        # A fan-out is not about the cwd repo; disabled repos are skipped inside.
        return False
    config = load_global_config(global_config_dir())
    if not config.disabled_repos:
        return False
    return disabled_entry(resolve_repo_root(explicit=args.repo), config) is not None


def _self_heal_preamble(args: argparse.Namespace) -> list[str]:
    config_dir = global_config_dir()
    messages = heal_global(config_dir)
    repo_root = resolve_repo_root(explicit=args.repo)
    if disabled_entry(repo_root, load_global_config(config_dir)) is not None:
        # Machine-level healing above still ran; a disabled repo is left alone.
        return messages
    mirror_line = heal_repo(repo_root, config_dir)
    if mirror_line is not None:
        messages.append(mirror_line)
    return messages + heal_gate(repo_root)


def _force_utf8_stdio() -> None:
    """Pin stdin/stdout/stderr to UTF-8 regardless of the locale.

    Harness hook payloads arrive as UTF-8 JSON and agent feedback must leave as
    UTF-8, but Windows pipes default to the ANSI code page. "replace" keeps a
    stray bad byte from crashing a hook or dropping all feedback. Test doubles
    (StringIO) are not TextIOWrappers and pass through untouched.
    """
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8", errors="replace")


def main(argv: Sequence[str] | None = None) -> int:
    _force_utf8_stdio()
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except ByorError as error:
        sys.stderr.write(f"byor: {error}\n")
        return error.exit_code


if __name__ == "__main__":
    sys.exit(main())
