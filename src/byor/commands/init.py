"""Initialize a repository for BYOR enforcement.

Initialization creates repository configuration, rule directories, ast-grep discovery, optional git
hooks, and optional gate files. The workflow also applies global defaults so a new repo converges
without scattering setup decisions across commands.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from byor.commands.doctor import quick_doctor_problems
from byor.commands.gate import install_gate
from byor.commands.profile import add_profile_to_local
from byor.commands.prompts import prompt_choice
from byor.config import (
    GlobalConfig,
    InitDefaults,
    LocalConfig,
    RepoConfig,
    disabled_entry,
    global_config_path,
    global_rules_dir,
    load_global_config,
    load_repo_config,
    local_config_path,
    register_repo,
    repo_config_path,
    repo_registry_path,
    rule_dir_relpaths,
    save_global_config,
    save_local_config,
    save_repo_config,
    save_repo_registry,
)
from byor.errors import ByorError, RepoNotInitializedError
from byor.io.gitio import git_output
from byor.io.output import write_line, write_lines
from byor.io.paths import display_path, global_config_dir, resolve_repo_root
from byor.rules.rules import discover_rule_files
from byor.rules.sync import load_canonical_rules, summarize_changes, sync_repo
from byor.scaffold.githooks import install_git_shims
from byor.scaffold.ignore import (
    ignore_file,
    write_ignore_block,
    write_rule_visibility_file,
)
from byor.scaffold.sgconfig import ensure_rule_dirs
from byor.scan.astgrep import resolve_ast_grep, scan_files

if TYPE_CHECKING:
    import argparse
    from pathlib import Path

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
    profile: str | None


def run_init(args: argparse.Namespace) -> int:
    repo_root = resolve_repo_root(explicit=args.repo)
    config_dir = global_config_dir()
    _require_enabled(repo_root, config_dir, interactive=not args.non_interactive)
    defaults = load_global_config(config_dir).init
    options = _options_from_args(args, defaults)
    write_lines(initialize_repo(repo_root, config_dir, options=options))
    write_line(f"Initialized BYOR in {repo_root}")
    return 0


def _require_enabled(repo_root: Path, config_dir: Path, *, interactive: bool) -> None:
    """Stop init in a disabled repo unless the user lifts the entry.

    Only an exact entry is removable here: a prefix entry covers other paths
    too, so lifting it on one child's confirmation would silently re-enable
    all of them — the message names the entry and `byor enable` instead.
    """
    config = load_global_config(config_dir)
    entry = disabled_entry(repo_root, config)
    if entry is None:
        return
    if entry != repo_root.resolve():
        msg = f"{repo_root} is disabled for byor by the entry {entry}; run `byor enable {entry}` to lift it"
        raise ByorError(msg)
    if not interactive:
        msg = f"{repo_root} is disabled for byor; run `byor enable {repo_root}` first"
        raise ByorError(msg)
    question = "This repository is disabled for byor — enable it and continue?"
    if prompt_choice(question, ("no", "yes"), default=0) != 1:
        msg = f"aborted: the repository stays disabled; run `byor enable {repo_root}` to re-enable it"
        raise ByorError(msg)
    config.disabled_repos.remove(entry)
    save_global_config(config_dir, config)
    write_line(f"Enabled byor in {repo_root}")


def initialize_repo(repo_root: Path, config_dir: Path, *, options: InitOptions) -> list[str]:
    messages: list[str] = []
    global_config = _bootstrap_global_dir(config_dir)
    repo_config = _ensure_repo_layout(repo_root, private=options.private)
    sgconfig_message = ensure_rule_dirs(
        repo_root / repo_config.paths.sgconfig,
        rule_dir_relpaths(repo_config.paths),
        replace=options.replace_sgconfig,
    )
    if sgconfig_message is not None:
        messages.append(sgconfig_message)
    if write_ignore_block(repo_root, private=options.private):
        target = display_path(ignore_file(repo_root, private=options.private), repo_root)
        messages.append(f"Wrote ignore block to {target}")
    sgconfig = repo_config.paths.sgconfig
    if options.private and git_output(repo_root, "ls-files", "--", sgconfig):
        messages.append(
            f"warning: {sgconfig} is already tracked; git will still show byor's changes to it despite private mode"
        )
    if options.git_hooks:
        messages.extend(install_git_shims(repo_root))
    if options.register and register_repo(repo_root, repo_registry_path(config_dir, global_config)):
        messages.append("Registered repository for `byor sync --all`")
    if options.profile is not None:
        add_profile_to_local(repo_root, global_config, name=options.profile)
        messages.append(f"Added profile '{options.profile}' to .byor/local.yml")
    _, sync_result = sync_repo(repo_root, load_canonical_rules(config_dir))
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


def _bootstrap_global_dir(config_dir: Path) -> GlobalConfig:
    if global_config_path(config_dir).is_file():
        config = load_global_config(config_dir)
    else:
        config = GlobalConfig()
        save_global_config(config_dir, config)
    global_rules_dir(config_dir, config).mkdir(parents=True, exist_ok=True)
    registry_path = repo_registry_path(config_dir, config)
    if not registry_path.is_file():
        save_repo_registry(registry_path, [])
    return config


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
    for rules_dir in rule_dir_relpaths(config.paths):
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
        profile=_resolve_profile(args, defaults),
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


def _resolve_profile(args: argparse.Namespace, defaults: InitDefaults) -> str | None:
    if args.no_profile:
        return None
    if args.profile is not None:
        return args.profile
    return defaults.profile
