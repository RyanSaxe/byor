"""Command-line entry point for byolsp."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from importlib.metadata import version

from byolsp.errors import ByolspError

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
        if name == "hook":
            actions = command.add_subparsers(dest="hook_action", required=True)
            actions.add_parser("install", help="Install agent integration files")
            actions.add_parser("uninstall", help="Remove BYOLSP-managed agent files")
    return parser


def run(args: argparse.Namespace) -> int:
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
