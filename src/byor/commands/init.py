"""Initialize a repository for BYOR enforcement.

Initialization creates repository configuration, rule directories, ast-grep discovery, optional git
hooks, and optional gate files. The workflow also applies global defaults so a new repo converges
without scattering setup decisions across commands.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from byor.commands.doctor import quick_doctor_problems
from byor.commands.gate import install_gate
from byor.commands.package import add_package_to_local, sync_or_restore_packages
from byor.commands.profile import add_profile_to_local
from byor.commands.prompts import prompt_choice
from byor.config import (
    GlobalConfig,
    InitDefaults,
    LocalConfig,
    RepoConfig,
    command_rule_dir_relpaths,
    disabled_entry,
    global_commands_dir,
    global_rules_dir,
    load_global_config,
    load_local_config,
    load_machine_state,
    load_repo_config,
    local_config_path,
    locked_state_file,
    register_repo,
    repo_config_path,
    repo_registry_path,
    rule_dir_relpaths,
    save_local_config,
    save_repo_config,
    save_repo_registry,
    updating_machine_state,
)
from byor.errors import ByorError, RepoNotInitializedError
from byor.io.gitio import git_output
from byor.io.output import write_line, write_lines
from byor.io.paths import display_path, global_config_dir, global_state_dir, resolve_init_root, same_path
from byor.rules.rules import discover_rule_files
from byor.rules.sync import summarize_changes
from byor.scaffold.githooks import install_git_shims
from byor.scaffold.ignore import (
    ignore_file,
    remove_private_block,
    write_ignore_block,
    write_rule_visibility_file,
)
from byor.scaffold.sgconfig import ensure_rule_dirs
from byor.scaffold.worktree import main_checkout_root
from byor.scan.astgrep import resolve_ast_grep, scan_files

if TYPE_CHECKING:
    import argparse

__all__ = (
    "InitOptions",
    "initialize_repo",
    "run_init",
)


@dataclass
class InitOptions:
    private: bool
    git_hooks: bool
    gate: bool
    register: bool
    replace_sgconfig: bool
    profiles: list[str]
    packages: list[str]


def run_init(args: argparse.Namespace) -> int:
    repo_root = resolve_init_root(explicit=args.repo)
    _refuse_home(repo_root)
    config_dir = global_config_dir()
    _require_enabled(repo_root, global_state_dir(), interactive=not args.non_interactive)
    defaults = load_global_config(config_dir).init
    options = _options_from_args(args, defaults)
    write_lines(initialize_repo(repo_root, config_dir, options=options))
    write_line(f"Initialized BYOR in {repo_root}")
    return 0


# ~/sgconfig.yml is ast-grep's global config, which byor keeps for the global rules. A repo there
# would add rule dirs that mirror those same rules, and every scan under home would then fail on
# duplicate IDs, with the post-edit hook failing open.
def _refuse_home(repo_root: Path) -> None:
    if repo_root.resolve() == Path.home().resolve():
        msg = (
            "byor init cannot set up your home directory, because ~/sgconfig.yml there holds"
            " byor's global rules; run byor init inside a repository instead"
        )
        raise ByorError(msg)


def _require_enabled(repo_root: Path, state_dir: Path, *, interactive: bool) -> None:
    """Stop init in a disabled repo unless the user lifts the entry.

    Only an exact entry is removable here: a prefix entry covers other paths
    too, so lifting it on one child's confirmation would silently re-enable
    all of them — the message names the entry and `byor enable` instead.
    """
    state = load_machine_state(state_dir)
    entry = disabled_entry(repo_root, state)
    if entry is None:
        return
    if not same_path(entry, repo_root.resolve()):
        msg = f"{repo_root} is disabled for byor by the entry {entry}; run `byor enable {entry}` to lift it"
        raise ByorError(msg)
    if not interactive:
        msg = f"{repo_root} is disabled for byor; run `byor enable {repo_root}` first"
        raise ByorError(msg)
    question = "This repository is disabled for byor — enable it and continue?"
    if prompt_choice(question, ("no", "yes"), default=0) != 1:
        msg = f"aborted: the repository stays disabled; run `byor enable {repo_root}` to re-enable it"
        raise ByorError(msg)
    with updating_machine_state(state_dir) as current:
        if entry in current.disabled_repos:
            current.disabled_repos.remove(entry)
    write_line(f"Enabled byor in {repo_root}")


def initialize_repo(repo_root: Path, config_dir: Path, *, options: InitOptions) -> list[str]:
    messages: list[str] = []
    global_config = load_global_config(config_dir)
    state_dir = global_state_dir()
    _bootstrap_global_dir(config_dir, state_dir, config=global_config)
    # The ignore block goes first, so a file byor cannot rewrite stops init
    # before it creates any personal file that git would then offer to commit.
    wrote_block = write_ignore_block(repo_root, private=options.private)
    repo_config = _ensure_repo_layout(repo_root, private=options.private)
    sgconfig_message = ensure_rule_dirs(
        repo_root / repo_config.paths.sgconfig,
        rule_dir_relpaths(repo_config.paths),
        replace=options.replace_sgconfig,
    )
    if sgconfig_message is not None:
        messages.append(sgconfig_message)
    if wrote_block:
        target = display_path(ignore_file(repo_root, private=options.private), repo_root)
        messages.append(f"Wrote ignore block to {target}")
    if not options.private:
        messages.extend(_drop_private_block(repo_root))
    sgconfig = repo_config.paths.sgconfig
    if options.private and git_output(repo_root, "ls-files", "--", sgconfig):
        messages.append(
            f"warning: {sgconfig} is already tracked; git will still show byor's changes to it despite private mode"
        )
    if options.git_hooks:
        messages.extend(install_git_shims(repo_root))
    # A linked worktree of a byor repo stays out of the registry, so a removed worktree never
    # becomes a dead entry: the fan-outs reach it through its main checkout's entry. A worktree
    # whose main checkout is not a byor repo has no such entry, so it registers like any repo.
    main_root = main_checkout_root(repo_root)
    registrable = main_root is None or not repo_config_path(main_root).is_file()
    if options.register and registrable and register_repo(repo_root, repo_registry_path(state_dir, global_config)):
        messages.append("Registered repository for `byor sync --all`")
    previous_packages = load_local_config(repo_root).packages
    messages.extend(_apply_profiles_and_packages(repo_root, config_dir, global_config=global_config, options=options))
    sync_result = sync_or_restore_packages(repo_root, config_dir, previous=previous_packages)
    if sync_result.changed:
        messages.append(f"Synced {summarize_changes(sync_result)}")
    if options.gate:
        messages.extend(install_gate(repo_root, config_dir, private=options.private))
        note = _existing_violations_note(repo_root, config_dir)
        if note is not None:
            messages.append(note)
    # Run doctor --quick, surfacing only the problems it finds.
    messages.extend(quick_doctor_problems(repo_root, config_dir))
    return messages


def _apply_profiles_and_packages(
    repo_root: Path,
    config_dir: Path,
    *,
    global_config: GlobalConfig,
    options: InitOptions,
) -> list[str]:
    messages: list[str] = []
    for name in options.profiles:
        add_profile_to_local(repo_root, global_config, name=name)
        messages.append(f"Added profile '{name}' to .byor/local.yml")
    for name in options.packages:
        add_package_to_local(repo_root, config_dir, global_config=global_config, name=name)
        messages.append(f"Installed package '{name}' in .byor/local.yml")
    return messages


def _existing_violations_note(repo_root: Path, config_dir: Path) -> str | None:
    """Tell the gate installer what the repo already violates, in one line.

    A lead who sees the gate land green can otherwise commit pre-existing
    violations unknowingly. One whole-repo scan; silent when the repo has no
    rules yet, when it is clean, or when the scan cannot run (doctor reports
    that on its own).
    """
    paths = load_repo_config(repo_root).paths
    if not any(discover_rule_files(repo_root / rules_dir) for rules_dir in rule_dir_relpaths(paths)):
        return None
    try:
        executable = resolve_ast_grep(load_global_config(config_dir).ast_grep_command)
        matches = scan_files(executable, repo_root, files=[]).matches
    except ByorError:
        return None
    if not matches:
        return None
    files = len({match.file for match in matches})
    violations_part = f"{len(matches)} existing violation{'' if len(matches) == 1 else 's'}"
    files_part = f"{files} file{'' if files == 1 else 's'}"
    return f"{violations_part} across {files_part}; run `byor agent-check` to see them"


# config.yml stays absent until the user writes one: its defaults are built in, and byor never
# writes the user's settings.
def _bootstrap_global_dir(config_dir: Path, state_dir: Path, *, config: GlobalConfig) -> None:
    for directory in (global_rules_dir(config_dir, config), global_commands_dir(config_dir, config)):
        # A read-only config dir supplied without these folders simply has no
        # global rules or command rules; init needs neither to exist.
        with contextlib.suppress(OSError):
            directory.mkdir(parents=True, exist_ok=True)
    registry_path = repo_registry_path(state_dir, config)
    if registry_path.is_file():
        return
    # Checked again under the lock, so a repo a concurrent init just registered is not wiped.
    with locked_state_file(registry_path):
        if not registry_path.is_file():
            save_repo_registry(registry_path, [])


# A shared init after a private one: the private block in `.git/info/exclude` would keep `.byor/`
# and sgconfig.yml ignored, so the shared setup's tracked config could never be committed.
def _drop_private_block(repo_root: Path) -> list[str]:
    result = remove_private_block(repo_root)
    if result == "removed":
        return ["Removed the private byor block from .git/info/exclude, so .byor/ and sgconfig.yml can be committed"]
    if result == "damaged":
        return [
            "warning: .git/info/exclude has a damaged byor block; remove it by hand so .byor/ and "
            "sgconfig.yml can be committed"
        ]
    return []


def _ensure_repo_layout(repo_root: Path, *, private: bool) -> RepoConfig:
    """Create .byor/ config files and rule directories.

    A private setup git-ignores the whole `.byor/` tree, so every rule
    directory — the shared project one included — needs a visibility file to
    stay loadable by ast-grep; a shared setup only needs it on the personal ones.
    """
    config = _load_or_default_repo_config(repo_root)
    if not repo_config_path(repo_root).is_file():
        save_repo_config(repo_root, config)
    if not local_config_path(repo_root).is_file():
        save_local_config(repo_root, LocalConfig())
    # Command rule dirs are created but never join sgconfig ruleDirs: they gate
    # shell commands, and file scans must not pick them up.
    for rules_dir in rule_dir_relpaths(config.paths) + command_rule_dir_relpaths(config.paths):
        gitkeep = repo_root / rules_dir / ".gitkeep"
        gitkeep.parent.mkdir(parents=True, exist_ok=True)
        gitkeep.touch(exist_ok=True)
    visible_dirs = [
        config.paths.personal_local_rules,
        config.paths.personal_global_rules,
        config.paths.personal_packages_rules,
    ]
    if private:
        visible_dirs.append(config.paths.project_rules)
    for rules_dir in visible_dirs:
        write_rule_visibility_file(repo_root / rules_dir)
    return config


def _load_or_default_repo_config(repo_root: Path) -> RepoConfig:
    try:
        return load_repo_config(repo_root)
    except RepoNotInitializedError:
        return RepoConfig(project_name=repo_root.name)


def _options_from_args(args: argparse.Namespace, defaults: InitDefaults) -> InitOptions:
    interactive = not args.non_interactive
    return InitOptions(
        private=_resolve_flag(
            explicit=args.private,
            default=defaults.private,
            interactive=interactive,
            question="Make this byor setup private (hide everything from git, don't commit)?",
            choices=("no, share it with the team", "yes, keep it to myself"),
        ),
        git_hooks=_resolve_flag(
            explicit=args.git_hooks,
            default=defaults.git_hooks,
            interactive=interactive,
            question="Install git hook shims that run `byor sync` after merge and checkout?",
        ),
        gate=_resolve_flag(
            explicit=args.gate,
            default=defaults.gate,
            interactive=interactive,
            question="Install a blocking gate (pre-commit + CI) that enforces these rules?",
        ),
        register=not args.no_register,
        replace_sgconfig=args.replace_sgconfig,
        profiles=_resolve_profiles(args, defaults),
        packages=_resolve_packages(args, defaults),
    )


def _resolve_flag(
    *,
    explicit: bool | None,
    default: bool | None,
    interactive: bool,
    question: str,
    choices: tuple[str, str] = ("no", "yes"),
) -> bool:
    # An explicit CLI value wins; otherwise the global-config init default
    # (None meaning "no") preselects the prompt or, non-interactively, decides.
    if explicit is not None:
        return explicit
    fallback = default if default is not None else False
    if not interactive:
        return fallback
    return prompt_choice(question, choices, default=1 if fallback else 0) == 1


def _resolve_profiles(args: argparse.Namespace, defaults: InitDefaults) -> list[str]:
    # Any explicit CLI selection replaces the global-config defaults; the
    # singular and plural spellings merge when both are configured.
    if args.no_profile:
        return []
    explicit = _named_selection(args.profile, args.profiles)
    if explicit:
        return explicit
    return _named_selection(defaults.profile, defaults.profiles)


def _resolve_packages(args: argparse.Namespace, defaults: InitDefaults) -> list[str]:
    if args.no_package:
        return []
    explicit = _named_selection(args.package, args.packages)
    if explicit:
        return explicit
    return _named_selection(defaults.package, defaults.packages)


def _named_selection(single: str | None, multiple: list[str] | None) -> list[str]:
    names = [] if single is None else [single]
    for name in multiple or []:
        if name not in names:
            names.append(name)
    return names
