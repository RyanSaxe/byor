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
        if name == "init":
            _add_init_arguments(command)
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


def run(args: argparse.Namespace) -> int:
    if args.command == "init":
        # Deferred so startup (--help, future hot paths) never pays for ruamel.
        from byolsp.init import run_init

        return run_init(args)
    raise ByolspError(f"'{args.command}' is not implemented yet")


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except ByolspError as error:
        print(f"byolsp: {error}", file=sys.stderr)
        return error.exit_code


if __name__ == "__main__":
    sys.exit(main())
