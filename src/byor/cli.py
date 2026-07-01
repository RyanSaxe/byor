"""Command-line entry point for byor."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from importlib.metadata import version
from pathlib import Path

from byor.agents.harness import HARNESS_CHOICES
from byor.agents.install import AGENT_CHOICES, run_hook
from byor.commands.doctor import run_doctor
from byor.commands.gate import heal_gate
from byor.commands.init import run_init
from byor.commands.install import run_install
from byor.commands.listing import run_list
from byor.commands.package import run_package
from byor.commands.profile import run_profile
from byor.errors import ByorError
from byor.io.paths import global_config_dir, resolve_repo_root
from byor.rules.commands import (
    run_add,
    run_edit,
    run_exclusion,
    run_promote,
    run_remove,
)
from byor.rules.sync import heal_global, heal_repo, run_sync
from byor.scan.agent_check import run_agent_check

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
    "profile": "List and apply local exclusion profiles",
    "package": "List and install opt-in rule/check packages",
    "list": "Show rules and where they come from",
    "agent-check": "Run ast-grep on changed files and render agent feedback",
    "hook": "Install or uninstall AI agent integrations",
}

REPO_COMMANDS = frozenset(COMMANDS) - {"install", "hook", "profile", "package"}
REPO_HELP = "Repository root (default: search upward from cwd)"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="byor",
        description="Custom ast-grep diagnostics, easy to set up, share, and expose to AI agents.",
    )
    parser.add_argument(
        "--version", action="version", version=f"byor {version('byor')}"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name, help_text in COMMANDS.items():
        command = subparsers.add_parser(name, help=help_text, description=help_text)
        # Commands that have not yet grown a --repo flag still get args.repo.
        command.set_defaults(repo=None)
        if name in REPO_COMMANDS:
            command.add_argument("--repo", type=Path, help=REPO_HELP)
        if name == "install":
            _add_install_arguments(command)
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
        if name in ("edit", "remove"):
            _add_rule_lookup_arguments(command, action=name)
        if name == "promote":
            _add_promote_arguments(command)
        if name in ("exclude", "include"):
            _add_exclusion_arguments(command)
        if name == "profile":
            _add_profile_arguments(command)
        if name == "package":
            _add_package_arguments(command)
        if name == "agent-check":
            _add_agent_check_arguments(command)
        if name == "hook":
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
    command.add_argument(
        "--all", action="store_true", help="Sync every registered repository"
    )
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
    command.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON"
    )
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
    command.add_argument(
        "--language", help="Language for the generated template (default: Python)"
    )
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
    """edit and remove share their signature: RULE_ID plus scope resolution."""
    command.add_argument(
        "rule_id", metavar="RULE_ID", help=f"ID of the rule to {action}"
    )
    command.add_argument(
        "--scope",
        choices=("project", "local", "global", "auto"),
        default="auto",
        help="Where to look for the rule (default: project, then local, then global)",
    )


def _add_promote_arguments(command: argparse.ArgumentParser) -> None:
    target = command.add_mutually_exclusive_group(required=True)
    target.add_argument(
        "rule_id", nargs="?", metavar="RULE_ID", help="ID of the rule to promote"
    )
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
    add = actions.add_parser(
        "add", help="Add a profile's exclusions to this repository"
    )
    add.add_argument("name", metavar="NAME", help="Profile name")
    add.add_argument("--repo", type=Path, help=REPO_HELP)


def _add_package_arguments(command: argparse.ArgumentParser) -> None:
    actions = command.add_subparsers(dest="package_action", required=True)
    actions.add_parser("list", help="List available packages")
    add = actions.add_parser(
        "add", help="Install a package's rules and checks into this repository"
    )
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
        "--quick", action="store_true", help="Skip recursive rule validation"
    )
    command.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON"
    )


# Commands whose body performs the heal itself, or must not self-heal: install
# (the command that sets global state up), init (runs a full sync as a step),
# and sync (whose --check variant must never write).
SELF_SYNCING_COMMANDS = frozenset({"install", "init", "sync", "profile", "package"})


def _is_hook_invocation(args: argparse.Namespace) -> bool:
    """A fail-open hook call (`agent-check --stdin-hook`) must never self-heal:
    a healing error would surface as a non-zero exit and block the agent.
    """
    return (
        args.command == "agent-check" and getattr(args, "stdin_hook", None) is not None
    )


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
    "agent-check": run_agent_check,
    "hook": run_hook,
}


def run(args: argparse.Namespace) -> int:
    if args.command not in SELF_SYNCING_COMMANDS and not _is_hook_invocation(args):
        try:
            heal_messages = _self_heal_preamble(args)
        except ByorError:
            # Doctor's job is reporting problems (e.g. a rule file that does
            # not parse stops sync); its own checks render them as FAIL rows.
            if args.command != "doctor":
                raise
            heal_messages = []
        for message in heal_messages:
            # stderr keeps stdout clean for JSON-emitting commands.
            print(message, file=sys.stderr)
    handler = _HANDLERS.get(args.command)
    if handler is None:
        raise ByorError(f"'{args.command}' is not implemented yet")
    return handler(args)


def _self_heal_preamble(args: argparse.Namespace) -> list[str]:
    """Heal machine-level state always, plus a stale repo's mirror and gate.

    Returns the repo heal lines (empty when nothing changed) so doctor can report
    what was healed; the global heal is silent. Uninitialized repos heal silently.
    """
    config_dir = global_config_dir()
    heal_global(config_dir)
    repo_root = resolve_repo_root(explicit=args.repo)
    mirror_line = heal_repo(repo_root, config_dir)
    messages = [mirror_line] if mirror_line is not None else []
    return messages + heal_gate(repo_root)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except ByorError as error:
        print(f"byor: {error}", file=sys.stderr)
        return error.exit_code


if __name__ == "__main__":
    sys.exit(main())
