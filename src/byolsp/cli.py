"""Command-line entry point for byolsp."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from importlib.metadata import version
from pathlib import Path
from typing import get_args

from byolsp.agents import AGENT_CHOICES
from byolsp.errors import ByolspError
from byolsp.ignore import IgnoreMode

COMMANDS = {
    "init": "Initialize BYOLSP in a repository",
    "sync": "Mirror enabled global rules into the repository",
    "doctor": "Validate installation health",
    "add": "Create a new rule in a scope",
    "edit": "Open an existing rule in $EDITOR",
    "promote": "Move a personal rule into shared project rules",
    "exclude": "Disable a global rule in this repository",
    "include": "Re-enable a previously excluded global rule",
    "list": "Show rules and where they come from",
    "agent-check": "Run ast-grep on changed files and render agent feedback",
    "hook": "Install or uninstall AI agent integrations",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="byolsp",
        description="Custom ast-grep diagnostics, easy to set up, share, and expose to AI agents.",
    )
    parser.add_argument(
        "--version", action="version", version=f"byolsp {version('byolsp')}"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name, help_text in COMMANDS.items():
        command = subparsers.add_parser(name, help=help_text, description=help_text)
        # Commands that have not yet grown a --repo flag still get args.repo.
        command.set_defaults(repo=None)
        if name == "init":
            _add_init_arguments(command)
        if name == "sync":
            _add_sync_arguments(command)
        if name == "doctor":
            _add_doctor_arguments(command)
        if name == "list":
            _add_list_arguments(command)
        if name == "add":
            _add_add_arguments(command)
        if name == "edit":
            _add_edit_arguments(command)
        if name == "promote":
            _add_promote_arguments(command)
        if name in ("exclude", "include"):
            _add_rule_id_arguments(command)
        if name == "agent-check":
            _add_agent_check_arguments(command)
        if name == "hook":
            actions = command.add_subparsers(dest="hook_action", required=True)
            actions.add_parser("install", help="Install agent integration files")
            actions.add_parser("uninstall", help="Remove BYOLSP-managed agent files")
    return parser


def _add_repo_argument(command: argparse.ArgumentParser) -> None:
    """Every repo-operating command accepts --repo with these semantics (SPEC 15)."""
    command.add_argument(
        "--repo", type=Path, help="Repository root (default: search upward from cwd)"
    )


def _add_init_arguments(command: argparse.ArgumentParser) -> None:
    _add_repo_argument(command)
    command.add_argument(
        "--agents",
        help=f"Comma-separated AI integrations: {', '.join(AGENT_CHOICES)}",
    )
    command.add_argument(
        "--ignore-mode",
        choices=get_args(IgnoreMode),
        help="Write ignore entries to .gitignore (project) or .git/info/exclude (local)",
    )
    command.add_argument(
        "--git-hooks",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Install post-merge/post-checkout shims that run `byolsp sync`",
    )
    command.add_argument(
        "--non-interactive",
        action="store_true",
        help="Use defaults instead of prompting",
    )
    command.add_argument(
        "--no-register",
        action="store_true",
        help="Skip registering this repository for `byolsp sync --all`",
    )
    command.add_argument(
        "--replace-sgconfig",
        action="store_true",
        help="Overwrite sgconfig.yml after saving a timestamped backup",
    )


def _add_sync_arguments(command: argparse.ArgumentParser) -> None:
    _add_repo_argument(command)
    command.add_argument(
        "--all", action="store_true", help="Sync every registered repository"
    )
    command.add_argument(
        "--check",
        action="store_true",
        help="Report staleness without writing; exit 3 when stale",
    )


def _add_list_arguments(command: argparse.ArgumentParser) -> None:
    _add_repo_argument(command)
    command.add_argument(
        "--scope",
        # Spelled out so --help stays import-light; keep in sync with listing.ListScope.
        choices=("project", "local", "global", "effective", "all"),
        default="effective",
        help="Which rules to show (default: effective, what ast-grep sees)",
    )
    command.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON"
    )


def _add_add_arguments(command: argparse.ArgumentParser) -> None:
    _add_repo_argument(command)
    command.add_argument(
        "--scope",
        choices=("project", "local", "global"),
        required=True,
        help="Where the new rule lives",
    )
    command.add_argument(
        "--language", help="Language for the generated template (default: Python)"
    )
    command.add_argument("--id", help="Rule ID for the generated template")
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


def _add_edit_arguments(command: argparse.ArgumentParser) -> None:
    _add_repo_argument(command)
    command.add_argument("rule_id", metavar="RULE_ID", help="ID of the rule to edit")
    command.add_argument(
        "--scope",
        choices=("project", "local", "global", "auto"),
        default="auto",
        help="Where to look for the rule (default: project, then local, then global)",
    )


def _add_promote_arguments(command: argparse.ArgumentParser) -> None:
    _add_repo_argument(command)
    command.add_argument("rule_id", metavar="RULE_ID", help="ID of the rule to promote")
    command.add_argument(
        "--from",
        dest="from_scope",
        choices=("local", "global"),
        required=True,
        help="Scope the rule currently lives in",
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


def _add_rule_id_arguments(command: argparse.ArgumentParser) -> None:
    _add_repo_argument(command)
    command.add_argument("rule_id", metavar="RULE_ID", help="ID of a global rule")


def _add_agent_check_arguments(command: argparse.ArgumentParser) -> None:
    _add_repo_argument(command)
    command.add_argument(
        "--files",
        nargs="+",
        type=Path,
        metavar="FILE",
        help="Files to scan (default: the whole repository)",
    )
    command.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output format (default: text)",
    )
    command.add_argument(
        "--max-results",
        type=int,
        metavar="N",
        help="Forwarded to ast-grep scan; also raises the 20-diagnostic render cap",
    )


def _add_doctor_arguments(command: argparse.ArgumentParser) -> None:
    _add_repo_argument(command)
    command.add_argument(
        "--quick", action="store_true", help="Skip recursive rule validation"
    )
    command.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON"
    )


# Commands whose body performs a full sync itself: init (step 8) and sync
# (whose --check variant must never write). Everything else self-heals first.
SELF_SYNCING_COMMANDS = frozenset({"init", "sync"})


def run(args: argparse.Namespace) -> int:
    if args.command not in SELF_SYNCING_COMMANDS:
        heal_message = _self_heal_preamble(args)
        if heal_message is not None:
            # stderr keeps stdout clean for JSON-emitting commands (SPEC 15.3/15.8).
            print(heal_message, file=sys.stderr)
    if args.command == "init":
        # Deferred so startup (--help, future hot paths) never pays for ruamel.
        from byolsp.init import run_init

        return run_init(args)
    if args.command == "sync":
        from byolsp.sync import run_sync

        return run_sync(args)
    if args.command == "doctor":
        from byolsp.doctor import run_doctor

        return run_doctor(args)
    if args.command == "list":
        from byolsp.listing import run_list

        return run_list(args)
    if args.command == "add":
        from byolsp.rule_commands import run_add

        return run_add(args)
    if args.command == "edit":
        from byolsp.rule_commands import run_edit

        return run_edit(args)
    if args.command == "promote":
        from byolsp.rule_commands import run_promote

        return run_promote(args)
    if args.command == "exclude":
        from byolsp.rule_commands import run_exclude

        return run_exclude(args)
    if args.command == "include":
        from byolsp.rule_commands import run_include

        return run_include(args)
    if args.command == "agent-check":
        from byolsp.agent_check import run_agent_check

        return run_agent_check(args)
    raise ByolspError(f"'{args.command}' is not implemented yet")


def _self_heal_preamble(args: argparse.Namespace) -> str | None:
    """SPEC 15: every repo-operating command heals a stale repo before running.

    Returns the one-line heal summary (None when nothing changed) so doctor
    can report what was healed (SPEC 15.3). Uninitialized repos heal silently.
    """
    from byolsp.paths import global_config_dir, resolve_repo_root
    from byolsp.sync import heal_repo

    return heal_repo(resolve_repo_root(explicit=args.repo), global_config_dir())


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except ByolspError as error:
        print(f"byolsp: {error}", file=sys.stderr)
        return error.exit_code


if __name__ == "__main__":
    sys.exit(main())
