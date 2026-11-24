"""Load and write BYOR configuration files.

Configuration spans repository, local, global, registry, machine-state, package, check, profile, and
init-default data. This module orchestrates each configuration layer and machine-state locking while
re-exporting the shared schema types from `byor.config_schema` so callers keep one typed interface.
"""

from __future__ import annotations

import os
import shutil
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from ruamel.yaml.comments import CommentedMap

from byor.config_schema import (
    CONFIG_VERSION,
    CheckDef,
    CommandCheckDef,
    InitDefaults,
    ProfileConfig,
    SectionValue,
    bool_field,
    check_defs,
    check_version,
    command_check_defs,
    fail_on,
    optional_bool,
    optional_positive_int,
    optional_string,
    package_names,
    profile_configs,
    section,
    string,
    string_list,
    update_section,
    write_checks,
    write_command_checks,
    write_init_defaults,
    write_profiles,
)
from byor.errors import ConfigError, RepoNotInitializedError
from byor.io.fsio import dangling_link_problem, write_text_atomic
from byor.io.lock import acquire_lock
from byor.io.paths import covers_path, same_path
from byor.io.yamlio import load_yaml_mapping, write_yaml_atomic

if TYPE_CHECKING:
    from collections.abc import Generator

__all__ = (
    "CheckDef",
    "CommandCheckDef",
    "CommandRuleScope",
    "GlobalConfig",
    "InitDefaults",
    "LocalConfig",
    "MachineState",
    "ProfileConfig",
    "RepoConfig",
    "RepoPaths",
    "command_rule_dir_relpaths",
    "command_rules_relpath",
    "disabled_entry",
    "global_commands_dir",
    "global_config_path",
    "global_packages_dir",
    "global_rules_dir",
    "installed_package_dir",
    "legacy_machine_state",
    "load_current_machine_state",
    "load_global_config",
    "load_local_config",
    "load_machine_state",
    "load_package_checks",
    "load_package_command_checks",
    "load_repo_config",
    "load_repo_registry",
    "local_config_path",
    "locked_state_file",
    "machine_state_path",
    "migrate_machine_state",
    "register_repo",
    "repo_config_path",
    "repo_registry_path",
    "rule_dir_relpaths",
    "save_global_config",
    "save_local_config",
    "save_machine_state",
    "save_repo_config",
    "save_repo_registry",
    "updating_machine_state",
)

# How long a state-writing command waits for another one, and when a lock counts as left behind.
# Each read-modify-write under the lock takes milliseconds.
STATE_LOCK_WAIT_SECONDS = 10.0
STATE_LOCK_STALE_SECONDS = 30.0

# A package's checks manifest; reserved at the package root, never a rule file.
PACKAGE_CHECKS_FILE = "checks.yml"

# A package's command rules; reserved at the package root, never file rules.
PACKAGE_COMMANDS_DIR = "commands"

# byor's record of one computer, under the state directory: never in config.yml, which the user owns.
MACHINE_STATE_FILE = "state.yml"

# Written last by the one-time move from config.yml. Its presence, not the state directory's,
# means the move is done, because the directory can exist first, for example as a link into a
# dotfiles repo, and a state.yml lost after the move must not bring the old records back.
STATE_MOVED_MARKER = ".moved-from-config"
STATE_MOVED_TEXT = (
    "byor has moved any agents, disabled paths and repo registry from config.yml here, and never moves them again.\n"
)


@dataclass
class RepoPaths:
    sgconfig: str = "sgconfig.yml"
    project_rules: str = ".byor/rules/project"
    personal_local_rules: str = ".byor/rules/personal/local"
    personal_global_rules: str = ".byor/rules/personal/global"
    personal_packages_rules: str = ".byor/rules/personal/packages"
    command_rules: str = ".byor/commands"
    """Root of the command-rule tree; scope subdirs mirror the rules tree by convention.

    Command rules gate shell commands before agents run them, so this tree must
    never appear in sgconfig `ruleDirs` — file scans must not pick them up.
    """


@dataclass
class RepoConfig:
    project_name: str | None = None
    paths: RepoPaths = field(default_factory=RepoPaths)
    checks: list[CheckDef] = field(default_factory=list)
    command_checks: list[CommandCheckDef] = field(default_factory=list)
    gate: bool = False
    """Whether byor keeps a byor-free pre-commit + CI gate regenerated for this repo."""
    gate_branch: str | None = None
    """Default branch recorded at gate install so regenerated CI never flaps across checkouts."""
    fail_on: Literal["all", "error"] = "all"
    """Gate strictness: 'all' escalates every rule to blocking; 'error' blocks on error-severity rules only."""


@dataclass
class LocalConfig:
    excluded_rule_ids: list[str] = field(default_factory=list)
    excluded_rule_tags: list[str] = field(default_factory=list)
    excluded_checks: list[str] = field(default_factory=list)
    excluded_check_tags: list[str] = field(default_factory=list)
    packages: list[str] = field(default_factory=list)
    """Global packages this user opted this repo into (see GlobalConfig.packages_path)."""
    vendored_scripts: list[str] = field(default_factory=list)
    """Gate scripts this checkout vendored from its own `~/...` sources, by repo relpath.

    Only these get re-vendored or checked for drift: on a teammate's machine a
    same-named home script is a different file and must never replace the
    committed copy.
    """


@dataclass
class GlobalConfig:
    rules_path: str = "rules"
    packages_path: str = "packages"
    commands_path: str = "commands"
    """Canonical global command rules; deliberately outside `rules_path`, which
    lands in the home sgconfig's `ruleDirs` and would leak these into file scans."""
    repos_path: str = "repos.yml"
    ast_grep_command: str = "auto"
    output_concise: bool = False
    """Trim text diagnostics to one line plus the fix; `--concise` forces it on."""
    output_max_diagnostics: int | None = None
    """Cap rendered diagnostics, noting how many more were found; None is unlimited."""
    worktrees_copy_ignored: bool = False
    """Copy every git-ignored file, not only byor's, from a main checkout into its new worktrees."""
    checks: list[CheckDef] = field(default_factory=list)
    command_checks: list[CommandCheckDef] = field(default_factory=list)
    init: InitDefaults = field(default_factory=InitDefaults)
    profiles: dict[str, ProfileConfig] = field(default_factory=dict)


@dataclass
class MachineState:
    """What byor records about one computer, kept in `state.yml` in the state directory.

    `byor install` and `byor hook install` add to `agents`, and `byor disable` and `byor enable`
    change `disabled_repos`. byor writes these itself, so they live beside `repos.yml` in the
    state directory and never in config.yml, which holds only the user's settings. Self-heal
    refreshes the integrations of every agent listed here.
    """

    agents: list[str] = field(default_factory=list)
    """The AI agents registered globally by `byor install` / `byor hook install`."""
    disabled_repos: list[Path] = field(default_factory=list)
    """Absolute paths `byor disable` turned off: repo roots or directory prefixes."""


def disabled_entry(repo_root: Path, state: MachineState) -> Path | None:
    """Return the `disabled_repos` entry covering `repo_root`, if any.

    An entry covers a path when it names it exactly or is an ancestor
    directory (path-aware, so `/foo` never covers `/foobar`); the first
    covering entry wins.
    """
    resolved = repo_root.resolve()
    for entry in state.disabled_repos:
        if covers_path(entry, resolved):
            return entry
    return None


def rule_dir_relpaths(paths: RepoPaths) -> list[str]:
    return [
        paths.project_rules,
        paths.personal_local_rules,
        paths.personal_global_rules,
        paths.personal_packages_rules,
    ]


CommandRuleScope = Literal["project", "local", "global", "packages"]

_COMMAND_SCOPE_SUBDIRS: dict[CommandRuleScope, str] = {
    "project": "project",
    "local": "personal/local",
    "global": "personal/global",
    "packages": "personal/packages",
}


def command_rules_relpath(paths: RepoPaths, scope: CommandRuleScope) -> str:
    return f"{paths.command_rules}/{_COMMAND_SCOPE_SUBDIRS[scope]}"


def command_rule_dir_relpaths(paths: RepoPaths) -> list[str]:
    return [command_rules_relpath(paths, scope) for scope in _COMMAND_SCOPE_SUBDIRS]


def repo_config_path(repo_root: Path) -> Path:
    return repo_root / ".byor" / "config.yml"


def local_config_path(repo_root: Path) -> Path:
    return repo_root / ".byor" / "local.yml"


def global_config_path(config_dir: Path) -> Path:
    return config_dir / "config.yml"


def global_rules_dir(config_dir: Path, config: GlobalConfig) -> Path:
    return config_dir / config.rules_path


def global_packages_dir(config_dir: Path, config: GlobalConfig) -> Path:
    return config_dir / config.packages_path


def global_commands_dir(config_dir: Path, config: GlobalConfig) -> Path:
    return config_dir / config.commands_path


def installed_package_dir(packages_root: Path, name: str) -> Path | None:
    """Return the package directory whose name is exactly `name`, or None.

    A case-insensitive filesystem would resolve `packages/pystrict` to a `PyStrict`
    folder, so sync would keep applying a package that `byor doctor` and `byor package
    add`, which compare names exactly, call not installed. Every lookup goes through
    this exact match instead, the same on every filesystem.
    """
    if not packages_root.is_dir():
        return None
    return next((entry for entry in packages_root.iterdir() if entry.name == name and entry.is_dir()), None)


def load_package_checks(config_dir: Path, config: GlobalConfig, *, name: str) -> list[CheckDef]:
    package = installed_package_dir(global_packages_dir(config_dir, config), name)
    path = package / PACKAGE_CHECKS_FILE if package is not None else None
    if path is None or not path.is_file():
        return []
    return check_defs(load_yaml_mapping(path), path)


def load_package_command_checks(config_dir: Path, config: GlobalConfig, *, name: str) -> list[CommandCheckDef]:
    package = installed_package_dir(global_packages_dir(config_dir, config), name)
    path = package / PACKAGE_CHECKS_FILE if package is not None else None
    if path is None or not path.is_file():
        return []
    return command_check_defs(load_yaml_mapping(path), path)


def repo_registry_path(state_dir: Path, config: GlobalConfig) -> Path:
    # `paths.repos` is a setting the user owns, but the file it names is one byor writes, so it
    # resolves against the state directory, never the config directory.
    return state_dir / config.repos_path


def load_repo_config(repo_root: Path) -> RepoConfig:
    path = repo_config_path(repo_root)
    if not path.is_file():
        msg = f"{repo_root} has no .byor/config.yml. Run `byor init` first."
        raise RepoNotInitializedError(msg)
    data = load_yaml_mapping(path)
    check_version(data, path)
    project = section(data, "project", path=path)
    paths = section(data, "paths", path=path)
    defaults = RepoPaths()
    return RepoConfig(
        project_name=optional_string(project, "name", path=path),
        paths=RepoPaths(
            sgconfig=string(paths, "sgconfig", default=defaults.sgconfig, path=path),
            project_rules=string(paths, "project_rules", default=defaults.project_rules, path=path),
            personal_local_rules=string(
                paths, "personal_local_rules", default=defaults.personal_local_rules, path=path
            ),
            personal_global_rules=string(
                paths, "personal_global_rules", default=defaults.personal_global_rules, path=path
            ),
            personal_packages_rules=string(
                paths, "personal_packages_rules", default=defaults.personal_packages_rules, path=path
            ),
            command_rules=string(paths, "command_rules", default=defaults.command_rules, path=path),
        ),
        checks=check_defs(data, path),
        command_checks=command_check_defs(data, path),
        gate=bool_field(data, "gate", path=path, default=False),
        gate_branch=optional_string(data, "gate_branch", path=path),
        fail_on=fail_on(data, path),
    )


def save_repo_config(repo_root: Path, config: RepoConfig) -> None:
    path = repo_config_path(repo_root)
    data = _load_or_new(path)
    data["version"] = CONFIG_VERSION
    update_section(data, "project", values={"name": config.project_name})
    update_section(
        data,
        "paths",
        values={
            "sgconfig": config.paths.sgconfig,
            "project_rules": config.paths.project_rules,
            "personal_local_rules": config.paths.personal_local_rules,
            "personal_global_rules": config.paths.personal_global_rules,
            "personal_packages_rules": config.paths.personal_packages_rules,
            "command_rules": config.paths.command_rules,
        },
    )
    write_checks(data, config.checks)
    write_command_checks(data, config.command_checks)
    if config.gate:
        data["gate"] = True
    if config.gate_branch is not None:
        data["gate_branch"] = config.gate_branch
    else:
        data.pop("gate_branch", None)
    if config.fail_on == "all":
        data.pop("fail_on", None)
    else:
        data["fail_on"] = config.fail_on
    write_yaml_atomic(path, data)


def load_local_config(repo_root: Path) -> LocalConfig:
    path = local_config_path(repo_root)
    if not path.is_file():
        return LocalConfig()
    data = load_yaml_mapping(path)
    check_version(data, path)
    global_section = section(data, "global", path=path)
    checks = section(data, "checks", path=path)
    return LocalConfig(
        excluded_rule_ids=string_list(global_section, "excluded_rule_ids", path=path),
        excluded_rule_tags=string_list(global_section, "excluded_tags", path=path),
        excluded_checks=string_list(checks, "excluded", path=path),
        excluded_check_tags=string_list(checks, "excluded_tags", path=path),
        packages=package_names(data, path=path),
        vendored_scripts=string_list(data, "vendored_scripts", path=path),
    )


def save_local_config(repo_root: Path, config: LocalConfig) -> None:
    path = local_config_path(repo_root)
    data = _load_or_new(path)
    data["version"] = CONFIG_VERSION
    update_section(
        data,
        "global",
        values={
            "excluded_rule_ids": list(config.excluded_rule_ids),
            "excluded_tags": list(config.excluded_rule_tags),
        },
    )
    update_section(
        data,
        "checks",
        values={
            "excluded": list(config.excluded_checks),
            "excluded_tags": list(config.excluded_check_tags),
        },
    )
    data["packages"] = list(config.packages)
    if config.vendored_scripts:
        data["vendored_scripts"] = list(config.vendored_scripts)
    else:
        data.pop("vendored_scripts", None)
    write_yaml_atomic(path, data)


def load_global_config(config_dir: Path) -> GlobalConfig:
    path = global_config_path(config_dir)
    if not path.is_file():
        return GlobalConfig()
    data = load_yaml_mapping(path)
    # The user writes config.yml, and byor no longer creates it, so a hand-written file
    # without `version`, or an empty one, means version 1; only a different version fails.
    if "version" in data:
        check_version(data, path)
    paths = section(data, "paths", path=path)
    ast_grep = section(data, "ast_grep", path=path)
    output = section(data, "output", path=path)
    worktrees = section(data, "worktrees", path=path)
    init = section(data, "init", path=path)
    defaults = GlobalConfig()
    return GlobalConfig(
        rules_path=string(paths, "rules", default=defaults.rules_path, path=path),
        packages_path=string(paths, "packages", default=defaults.packages_path, path=path),
        commands_path=string(paths, "commands", default=defaults.commands_path, path=path),
        repos_path=string(paths, "repos", default=defaults.repos_path, path=path),
        ast_grep_command=string(ast_grep, "command", default=defaults.ast_grep_command, path=path),
        output_concise=bool_field(output, "concise", path=path, default=defaults.output_concise),
        output_max_diagnostics=optional_positive_int(output, "max_diagnostics", path=path),
        worktrees_copy_ignored=bool_field(
            worktrees, "copy_ignored", path=path, default=defaults.worktrees_copy_ignored
        ),
        checks=check_defs(data, path),
        command_checks=command_check_defs(data, path),
        init=InitDefaults(
            private=optional_bool(init, "private", path=path),
            git_hooks=optional_bool(init, "git_hooks", path=path),
            gate=optional_bool(init, "gate", path=path),
            profile=optional_string(init, "profile", path=path),
            profiles=string_list(init, "profiles", path=path),
            package=optional_string(init, "package", path=path),
            packages=string_list(init, "packages", path=path),
        ),
        profiles=profile_configs(data, path),
    )


def save_global_config(config_dir: Path, config: GlobalConfig) -> None:
    """Write the user's settings to config.yml.

    No byor command calls this: config.yml belongs to the user, and byor keeps what it records in
    the state directory instead (see `save_machine_state`). It exists so tests and other tools can
    write the file byor reads, in the format `load_global_config` expects.
    """
    path = global_config_path(config_dir)
    data = _load_or_new(path)
    data["version"] = CONFIG_VERSION
    update_section(
        data,
        "paths",
        values={
            "rules": config.rules_path,
            "packages": config.packages_path,
            "commands": config.commands_path,
            "repos": config.repos_path,
        },
    )
    update_section(data, "ast_grep", values={"command": config.ast_grep_command})
    output_values: dict[str, SectionValue] = {"concise": config.output_concise}
    if config.output_max_diagnostics is not None:
        output_values["max_diagnostics"] = config.output_max_diagnostics
    update_section(data, "output", values=output_values)
    update_section(data, "worktrees", values={"copy_ignored": config.worktrees_copy_ignored})
    write_checks(data, config.checks)
    write_command_checks(data, config.command_checks)
    write_init_defaults(data, config.init)
    write_profiles(data, config.profiles)
    write_yaml_atomic(path, data)


def machine_state_path(state_dir: Path) -> Path:
    return state_dir / MACHINE_STATE_FILE


def load_machine_state(state_dir: Path) -> MachineState:
    path = machine_state_path(state_dir)
    if not path.is_file():
        return MachineState()
    data = load_yaml_mapping(path)
    check_version(data, path)
    # A hand edit or an old config can list an agent twice; once is what it means.
    return MachineState(
        agents=list(dict.fromkeys(string_list(data, "agents", path=path))),
        disabled_repos=[Path(entry) for entry in string_list(data, "disabled_repos", path=path)],
    )


def save_machine_state(state_dir: Path, state: MachineState) -> None:
    path = machine_state_path(state_dir)
    data = _load_or_new(path)
    data["version"] = CONFIG_VERSION
    data["agents"] = list(state.agents)
    data["disabled_repos"] = [str(entry) for entry in state.disabled_repos]
    _write_state_file(path, data)


@contextmanager
def locked_state_file(path: Path) -> Generator[None, None, None]:
    """Hold the lock for one read-modify-write of the state file `path`.

    Commands and agent hooks run at the same time, and each writer reads the whole file, changes
    it and writes it back, so without the lock the last writer silently drops the others' changes.
    The lock is `path` with `.lock` appended. Raises ConfigError naming the path when the state
    directory cannot be written, and when another byor process holds the lock for
    `STATE_LOCK_WAIT_SECONDS`, so the user can run the command again.
    """
    lock = path.with_name(f"{path.name}.lock")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        held = acquire_lock(lock, wait_seconds=STATE_LOCK_WAIT_SECONDS, stale_seconds=STATE_LOCK_STALE_SECONDS)
    except OSError as error:
        msg = f"cannot write byor's state to {path}: {error}"
        raise ConfigError(msg) from error
    if not held:
        msg = f"another byor command held {lock} for {STATE_LOCK_WAIT_SECONDS:.0f} seconds; run the command again"
        raise ConfigError(msg)
    try:
        yield
    finally:
        lock.unlink(missing_ok=True)


@contextmanager
def updating_machine_state(state_dir: Path) -> Generator[MachineState, None, None]:
    """Load `state.yml` under its lock, let the caller change it, and save it.

    Every command that changes the agents or disabled paths goes through this, so two that run
    together both keep their change. The body sees the state as of the moment the lock was taken,
    so it must decide what to change from that state, not from one read earlier:

        with updating_machine_state(state_dir) as state:
            if target not in state.disabled_repos:
                state.disabled_repos.append(target)

    Nothing is saved when the body raises, and the lock is released either way. Raises the same
    ConfigError as `locked_state_file`.
    """
    with locked_state_file(machine_state_path(state_dir)):
        state = load_machine_state(state_dir)
        yield state
        save_machine_state(state_dir, state)


def migrate_machine_state(config_dir: Path, state_dir: Path) -> None:
    """Copy what older byor versions recorded under the config directory into `state_dir`, once.

    Older versions kept `ai.agents` and `disabled_repos` in config.yml and wrote `repos.yml` beside
    it. The first run of this version copies all three into the state directory and leaves the old
    file and keys as they are, because config.yml belongs to the user; `byor doctor` then asks the
    user to delete them. The `STATE_MOVED_MARKER` file, written last, marks the move done, so a
    `state.yml` lost later starts empty instead of bringing back agents and disabled paths the
    user has changed since, and later runs cost one stat. A state directory that does not exist
    yet is built under a temporary name and renamed into place, so a failed move leaves nothing
    behind and runs again on the next command; concurrent first runs are safe, because only one
    rename can create the directory. A directory that already exists, such as a link into a
    dotfiles repo, is filled in place under the state lock instead.

    Raises:
        ConfigError: an old record cannot be read or parsed, or the state directory cannot be
            created, naming the file or directory at fault.

    """
    if not _move_pending(state_dir):
        return
    config_path = global_config_path(config_dir)
    try:
        config = load_global_config(config_dir)
        legacy = _legacy_machine_state(config_dir)
    except OSError as error:
        msg = f"cannot read {config_path}: {error}"
        raise ConfigError(msg) from error
    legacy_registry = config_dir / config.repos_path
    registry_text = None
    # An absolute `paths.repos` names one file from both directories, so there is nothing to copy.
    if legacy_registry != repo_registry_path(state_dir, config):
        registry_text = _read_legacy_text(legacy_registry)
    if state_dir.is_dir():
        _fill_state_dir(state_dir, config=config, state=legacy, registry_text=registry_text)
    else:
        _create_state_dir(state_dir, config=config, state=legacy, registry_text=registry_text)


def load_current_machine_state(config_dir: Path, state_dir: Path) -> MachineState:
    """Return the machine state in force, reading the old config.yml records until the move happens.

    Hooks carry on when the one-time move fails, and until it succeeds an older byor's
    `disabled_repos` and `ai.agents` in config.yml are still the user's records, so a repo they
    disabled stays disabled. Commands stop when the move fails, so for them this is
    `load_machine_state`. Raises ConfigError for a malformed record or a state directory byor
    cannot read, which hooks treat as an internal error and skip.
    """
    if _move_pending(state_dir):
        return _legacy_machine_state(config_dir)
    return load_machine_state(state_dir)


def _move_pending(state_dir: Path) -> bool:
    # A dangling link reads as "no state directory", and the move would then
    # fail renaming onto the link with an error that never names it.
    if problem := dangling_link_problem(state_dir):
        msg = f"cannot use byor's state directory: {problem}"
        raise ConfigError(msg)
    try:
        if not state_dir.exists():
            return True
        if (state_dir / STATE_MOVED_MARKER).exists():
            return False
        # A directory written before byor kept the marker already holds byor's own state.yml.
        return not machine_state_path(state_dir).exists()
    except OSError as error:
        msg = f"cannot read byor's state directory {state_dir}: {error}"
        raise ConfigError(msg) from error


def _fill_state_dir(state_dir: Path, *, config: GlobalConfig, state: MachineState, registry_text: str | None) -> None:
    with locked_state_file(machine_state_path(state_dir)):
        # Another byor process may have finished the move while this one waited for the lock.
        if not _move_pending(state_dir):
            return
        registry = repo_registry_path(state_dir, config)
        try:
            if registry_text is not None and not registry.exists():
                write_text_atomic(registry, registry_text)
            save_machine_state(state_dir, state)
            write_text_atomic(state_dir / STATE_MOVED_MARKER, STATE_MOVED_TEXT)
        except OSError as error:
            msg = f"cannot write byor's state to {state_dir}: {error}"
            raise ConfigError(msg) from error


def _read_legacy_text(path: Path) -> str | None:
    try:
        if not path.is_file():
            return None
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        msg = f"{path}: not valid UTF-8: {error}"
        raise ConfigError(msg) from error
    except OSError as error:
        msg = f"cannot read {path}: {error}"
        raise ConfigError(msg) from error


def _create_state_dir(state_dir: Path, *, config: GlobalConfig, state: MachineState, registry_text: str | None) -> None:
    staging = state_dir.with_name(f".{state_dir.name}.moving-{os.getpid()}")
    try:
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True)
        if registry_text is not None:
            write_text_atomic(repo_registry_path(staging, config), registry_text)
        save_machine_state(staging, state)
        write_text_atomic(staging / STATE_MOVED_MARKER, STATE_MOVED_TEXT)
        staging.rename(state_dir)
    except (OSError, ConfigError) as error:
        shutil.rmtree(staging, ignore_errors=True)
        # Another byor process finished the move first; its directory is the one to keep.
        if state_dir.is_dir():
            return
        msg = f"cannot write byor's state to {state_dir}: {error}"
        raise ConfigError(msg) from error


def legacy_machine_state(config_dir: Path, state_dir: Path) -> list[str]:
    """Name the entries older byor versions wrote under the config directory that remain there.

    Returns labels such as "`ai.agents` in config.yml" and "repos.yml" for doctor to report; the
    registry's label is its `paths.repos` value, so a custom location is named as configured.
    byor reads none of them after `migrate_machine_state` copied them, so an edit to one silently
    does nothing, which is why doctor asks the user to delete them. Raises ConfigError for a
    malformed config.yml, as `load_global_config` does.
    """
    leftovers: list[str] = []
    path = global_config_path(config_dir)
    if path.is_file():
        data = load_yaml_mapping(path)
        if "agents" in section(data, "ai", path=path):
            leftovers.append("`ai.agents` in config.yml")
        if "disabled_repos" in data:
            leftovers.append("`disabled_repos` in config.yml")
    config = load_global_config(config_dir)
    legacy_registry = config_dir / config.repos_path
    # An absolute `paths.repos` names the same file from both directories, and that file is live.
    if legacy_registry.is_file() and legacy_registry != repo_registry_path(state_dir, config):
        leftovers.append(Path(config.repos_path).as_posix())
    return leftovers


def _legacy_machine_state(config_dir: Path) -> MachineState:
    path = global_config_path(config_dir)
    if not path.is_file():
        return MachineState()
    data = load_yaml_mapping(path)
    return MachineState(
        agents=string_list(section(data, "ai", path=path), "agents", path=path),
        disabled_repos=[Path(entry) for entry in string_list(data, "disabled_repos", path=path)],
    )


def load_repo_registry(path: Path) -> list[Path]:
    if not path.is_file():
        return []
    data = load_yaml_mapping(path)
    check_version(data, path)
    return [Path(entry) for entry in string_list(data, "repos", path=path)]


def save_repo_registry(path: Path, repos: list[Path]) -> None:
    data = _load_or_new(path)
    data["version"] = CONFIG_VERSION
    data["repos"] = [str(repo) for repo in repos]
    _write_state_file(path, data)


def _load_or_new(path: Path) -> CommentedMap:
    return load_yaml_mapping(path) if path.is_file() else CommentedMap()


# The state directory is the one place byor writes on every computer, so an
# unwritable one must read as a config error naming the path, not a traceback.
def _write_state_file(path: Path, data: CommentedMap) -> None:
    try:
        write_yaml_atomic(path, data)
    except OSError as error:
        msg = f"cannot write byor's state to {path}: {error}"
        raise ConfigError(msg) from error


def register_repo(repo_root: Path, registry_path: Path) -> bool:
    # A path spelled in another case on a case-insensitive volume is the same repo.
    resolved = repo_root.resolve()
    with locked_state_file(registry_path):
        repos = load_repo_registry(registry_path)
        if any(same_path(resolved, repo) for repo in repos):
            return False
        repos.append(resolved)
        save_repo_registry(registry_path, repos)
    return True
