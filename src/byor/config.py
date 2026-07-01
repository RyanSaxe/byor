"""Load and write BYOR configuration files.

Configuration spans repository, local, global, registry, package, check, profile, and init-default
data. This module centralizes YAML parsing and validation so command modules operate on typed
dataclasses instead of raw mappings.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from pathlib import Path

from ruamel.yaml.comments import CommentedMap

from byor.errors import ConfigError, RepoNotInitializedError
from byor.io.yamlio import load_yaml_mapping, write_yaml_atomic

__all__ = (
    "CheckDef",
    "GlobalConfig",
    "InitDefaults",
    "LocalConfig",
    "ProfileConfig",
    "RepoConfig",
    "RepoPaths",
    "global_config_path",
    "global_packages_dir",
    "global_rules_dir",
    "load_global_config",
    "load_local_config",
    "load_package_checks",
    "load_repo_config",
    "load_repo_registry",
    "local_config_path",
    "register_repo",
    "repo_config_path",
    "repo_registry_path",
    "rule_dir_relpaths",
    "save_global_config",
    "save_local_config",
    "save_repo_config",
    "save_repo_registry",
)

CONFIG_VERSION = 1

# A package's checks manifest; reserved at the package root, never a rule file.
PACKAGE_CHECKS_FILE = "checks.yml"

# The value types a managed config section may hold, written back in place.
SectionValue = str | None | bool | int | list[str]


@dataclass
class RepoPaths:
    sgconfig: str = "sgconfig.yml"
    project_rules: str = ".byor/rules/project"
    personal_local_rules: str = ".byor/rules/personal/local"
    personal_global_rules: str = ".byor/rules/personal/global"
    personal_packages_rules: str = ".byor/rules/personal/packages"


@dataclass(frozen=True)
class CheckDef:
    """An extra command-line check run after ast-grep.

    `run` is shlex-split into argv and run without a shell; the in-scope files
    are appended as trailing arguments, so the command must accept a list of
    file paths. A leading `~`/`~/` in the command expands to the user's home.
    `extensions` (no dots) filters which files trigger the check.
    """

    name: str
    extensions: list[str]
    run: str
    tags: list[str] = field(default_factory=list)


@dataclass
class RepoConfig:
    project_name: str | None = None
    paths: RepoPaths = field(default_factory=RepoPaths)
    checks: list[CheckDef] = field(default_factory=list)
    gate: bool = False
    """Whether byor keeps a byor-free pre-commit + CI gate regenerated for this repo."""


@dataclass
class LocalConfig:
    excluded_rule_ids: list[str] = field(default_factory=list)
    excluded_rule_tags: list[str] = field(default_factory=list)
    excluded_checks: list[str] = field(default_factory=list)
    excluded_check_tags: list[str] = field(default_factory=list)
    packages: list[str] = field(default_factory=list)
    """Global packages this user opted this repo into (see GlobalConfig.packages_path)."""


@dataclass
class InitDefaults:
    private: bool | None = None
    git_hooks: bool | None = None
    gate: bool | None = None
    profile: str | None = None


@dataclass(frozen=True)
class ProfileConfig:
    description: str | None = None
    excluded_rule_ids: list[str] = field(default_factory=list)
    excluded_rule_tags: list[str] = field(default_factory=list)
    excluded_checks: list[str] = field(default_factory=list)
    excluded_check_tags: list[str] = field(default_factory=list)


@dataclass
class GlobalConfig:
    rules_path: str = "rules"
    packages_path: str = "packages"
    repos_path: str = "repos.yml"
    ast_grep_command: str = "auto"
    output_concise: bool = False
    """Trim text diagnostics to one line plus the fix; `--concise` forces it on."""
    output_max_diagnostics: int | None = None
    """Cap rendered diagnostics, noting how many more were found; None is unlimited."""
    agents: list[str] = field(default_factory=list)
    """The AI agents registered globally by `byor install` / `byor hook install`."""
    checks: list[CheckDef] = field(default_factory=list)
    init: InitDefaults = field(default_factory=InitDefaults)
    profiles: dict[str, ProfileConfig] = field(default_factory=dict)


def rule_dir_relpaths(paths: RepoPaths) -> list[str]:
    return [
        paths.project_rules,
        paths.personal_local_rules,
        paths.personal_global_rules,
        paths.personal_packages_rules,
    ]


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


def load_package_checks(config_dir: Path, config: GlobalConfig, *, name: str) -> list[CheckDef]:
    path = global_packages_dir(config_dir, config) / name / PACKAGE_CHECKS_FILE
    if not path.is_file():
        return []
    return _check_defs(load_yaml_mapping(path), path)


def repo_registry_path(config_dir: Path, config: GlobalConfig) -> Path:
    return config_dir / config.repos_path


def load_repo_config(repo_root: Path) -> RepoConfig:
    path = repo_config_path(repo_root)
    if not path.is_file():
        msg = f"{repo_root} has no .byor/config.yml. Run `byor init` first."
        raise RepoNotInitializedError(msg)
    data = load_yaml_mapping(path)
    _check_version(data, path)
    project = _section(data, "project", path=path)
    paths = _section(data, "paths", path=path)
    defaults = RepoPaths()
    return RepoConfig(
        project_name=_optional_string(project, "name", path=path),
        paths=RepoPaths(
            sgconfig=_string(paths, "sgconfig", default=defaults.sgconfig, path=path),
            project_rules=_string(paths, "project_rules", default=defaults.project_rules, path=path),
            personal_local_rules=_string(
                paths, "personal_local_rules", default=defaults.personal_local_rules, path=path
            ),
            personal_global_rules=_string(
                paths, "personal_global_rules", default=defaults.personal_global_rules, path=path
            ),
            personal_packages_rules=_string(
                paths, "personal_packages_rules", default=defaults.personal_packages_rules, path=path
            ),
        ),
        checks=_check_defs(data, path),
        gate=_bool(data, "gate", path=path, default=False),
    )


def save_repo_config(repo_root: Path, config: RepoConfig) -> None:
    path = repo_config_path(repo_root)
    data = _load_or_new(path)
    data["version"] = CONFIG_VERSION
    _update_section(data, "project", values={"name": config.project_name})
    _update_section(
        data,
        "paths",
        values={
            "sgconfig": config.paths.sgconfig,
            "project_rules": config.paths.project_rules,
            "personal_local_rules": config.paths.personal_local_rules,
            "personal_global_rules": config.paths.personal_global_rules,
            "personal_packages_rules": config.paths.personal_packages_rules,
        },
    )
    _write_checks(data, config.checks)
    if config.gate:
        data["gate"] = True
    write_yaml_atomic(path, data)


def load_local_config(repo_root: Path) -> LocalConfig:
    path = local_config_path(repo_root)
    if not path.is_file():
        return LocalConfig()
    data = load_yaml_mapping(path)
    _check_version(data, path)
    section = _section(data, "global", path=path)
    checks = _section(data, "checks", path=path)
    return LocalConfig(
        excluded_rule_ids=_string_list(section, "excluded_rule_ids", path=path),
        excluded_rule_tags=_string_list(section, "excluded_tags", path=path),
        excluded_checks=_string_list(checks, "excluded", path=path),
        excluded_check_tags=_string_list(checks, "excluded_tags", path=path),
        packages=_string_list(data, "packages", path=path),
    )


def save_local_config(repo_root: Path, config: LocalConfig) -> None:
    path = local_config_path(repo_root)
    data = _load_or_new(path)
    data["version"] = CONFIG_VERSION
    _update_section(
        data,
        "global",
        values={
            "excluded_rule_ids": list(config.excluded_rule_ids),
            "excluded_tags": list(config.excluded_rule_tags),
        },
    )
    _update_section(
        data,
        "checks",
        values={
            "excluded": list(config.excluded_checks),
            "excluded_tags": list(config.excluded_check_tags),
        },
    )
    data["packages"] = list(config.packages)
    write_yaml_atomic(path, data)


def load_global_config(config_dir: Path) -> GlobalConfig:
    path = global_config_path(config_dir)
    if not path.is_file():
        return GlobalConfig()
    data = load_yaml_mapping(path)
    _check_version(data, path)
    paths = _section(data, "paths", path=path)
    ast_grep = _section(data, "ast_grep", path=path)
    output = _section(data, "output", path=path)
    ai = _section(data, "ai", path=path)
    init = _section(data, "init", path=path)
    defaults = GlobalConfig()
    return GlobalConfig(
        rules_path=_string(paths, "rules", default=defaults.rules_path, path=path),
        packages_path=_string(paths, "packages", default=defaults.packages_path, path=path),
        repos_path=_string(paths, "repos", default=defaults.repos_path, path=path),
        ast_grep_command=_string(ast_grep, "command", default=defaults.ast_grep_command, path=path),
        output_concise=_bool(output, "concise", path=path, default=defaults.output_concise),
        output_max_diagnostics=_optional_positive_int(output, "max_diagnostics", path=path),
        agents=_string_list(ai, "agents", path=path),
        checks=_check_defs(data, path),
        init=InitDefaults(
            private=_optional_bool(init, "private", path=path),
            git_hooks=_optional_bool(init, "git_hooks", path=path),
            gate=_optional_bool(init, "gate", path=path),
            profile=_optional_string(init, "profile", path=path),
        ),
        profiles=_profile_configs(data, path),
    )


def save_global_config(config_dir: Path, config: GlobalConfig) -> None:
    path = global_config_path(config_dir)
    data = _load_or_new(path)
    data["version"] = CONFIG_VERSION
    _update_section(
        data,
        "paths",
        values={
            "rules": config.rules_path,
            "packages": config.packages_path,
            "repos": config.repos_path,
        },
    )
    _update_section(data, "ast_grep", values={"command": config.ast_grep_command})
    output_values: dict[str, SectionValue] = {"concise": config.output_concise}
    if config.output_max_diagnostics is not None:
        output_values["max_diagnostics"] = config.output_max_diagnostics
    _update_section(data, "output", values=output_values)
    _update_section(data, "ai", values={"agents": list(config.agents)})
    _write_checks(data, config.checks)
    _write_init_defaults(data, config.init)
    _write_profiles(data, config.profiles)
    write_yaml_atomic(path, data)


def load_repo_registry(path: Path) -> list[Path]:
    if not path.is_file():
        return []
    data = load_yaml_mapping(path)
    _check_version(data, path)
    return [Path(entry) for entry in _string_list(data, "repos", path=path)]


def save_repo_registry(path: Path, repos: list[Path]) -> None:
    data = _load_or_new(path)
    data["version"] = CONFIG_VERSION
    data["repos"] = [str(repo) for repo in repos]
    write_yaml_atomic(path, data)


def register_repo(repo_root: Path, registry_path: Path) -> bool:
    repos = load_repo_registry(registry_path)
    resolved = repo_root.resolve()
    if resolved in repos:
        return False
    repos.append(resolved)
    save_repo_registry(registry_path, repos)
    return True


def _load_or_new(path: Path) -> CommentedMap:
    return load_yaml_mapping(path) if path.is_file() else CommentedMap()


def _update_section(data: CommentedMap, key: str, *, values: dict[str, SectionValue]) -> None:
    section = data.get(key)
    if not isinstance(section, CommentedMap):
        section = CommentedMap()
        data[key] = section
    for name, value in values.items():
        section[name] = value


def _check_version(data: CommentedMap, path: Path) -> None:
    if data.get("version") != CONFIG_VERSION:
        msg = f"{path}: expected `version: {CONFIG_VERSION}`"
        raise ConfigError(msg)


def _section(data: CommentedMap, key: str, *, path: Path) -> CommentedMap:
    value = data.get(key)
    if value is None:
        return CommentedMap()
    if not isinstance(value, CommentedMap):
        msg = f"{path}: expected '{key}' to be a mapping"
        raise ConfigError(msg)
    return value


def _string(section: CommentedMap, key: str, *, default: str, path: Path) -> str:
    value = section.get(key, default)
    if not isinstance(value, str):
        msg = f"{path}: expected '{key}' to be a string"
        raise ConfigError(msg)
    return value


def _bool(section: CommentedMap, key: str, *, path: Path, default: bool) -> bool:
    value = section.get(key, default)
    if not isinstance(value, bool):
        msg = f"{path}: expected '{key}' to be a boolean"
        raise ConfigError(msg)
    return value


def _optional_string(section: CommentedMap, key: str, *, path: Path) -> str | None:
    value = section.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        msg = f"{path}: expected '{key}' to be a string or null"
        raise ConfigError(msg)
    return value


def _check_defs(data: CommentedMap, path: Path) -> list[CheckDef]:
    value = data.get("checks")
    if value is None:
        return []
    if not isinstance(value, list):
        msg = f"{path}: expected 'checks' to be a list of mappings"
        raise ConfigError(msg)
    return [_check_def(entry, path) for entry in value]


def _check_def(entry: object, path: Path) -> CheckDef:
    if not isinstance(entry, CommentedMap):
        msg = f"{path}: expected each check to be a mapping"
        raise ConfigError(msg)
    name = _string(entry, "name", default="", path=path)
    run = _string(entry, "run", default="", path=path)
    if not name or not run:
        msg = f"{path}: each check needs a non-empty 'name' and 'run'"
        raise ConfigError(msg)
    try:
        argv = shlex.split(run)
    except ValueError as error:
        msg = f"{path}: check '{name}' has an invalid 'run': {error}"
        raise ConfigError(msg) from error
    if not argv:
        msg = f"{path}: check '{name}' has an empty 'run' command"
        raise ConfigError(msg)
    return CheckDef(
        name=name,
        extensions=_string_list(entry, "extensions", path=path),
        run=run,
        tags=_string_list(entry, "tags", path=path),
    )


def _write_checks(data: CommentedMap, checks: list[CheckDef]) -> None:
    rendered = []
    for check in checks:
        entry: dict[str, str | list[str]] = {
            "name": check.name,
            "extensions": list(check.extensions),
            "run": check.run,
        }
        if check.tags:
            entry["tags"] = list(check.tags)
        rendered.append(entry)
    data["checks"] = rendered


def _write_init_defaults(data: CommentedMap, init: InitDefaults) -> None:
    values: dict[str, SectionValue] = {}
    if init.private is not None:
        values["private"] = init.private
    if init.git_hooks is not None:
        values["git_hooks"] = init.git_hooks
    if init.gate is not None:
        values["gate"] = init.gate
    if init.profile is not None:
        values["profile"] = init.profile
    if values:
        _update_section(data, "init", values=values)


def _profile_configs(data: CommentedMap, path: Path) -> dict[str, ProfileConfig]:
    value = data.get("profiles")
    if value is None:
        return {}
    if not isinstance(value, CommentedMap):
        msg = f"{path}: expected 'profiles' to be a mapping"
        raise ConfigError(msg)
    profiles: dict[str, ProfileConfig] = {}
    for name, entry in value.items():
        if not isinstance(name, str):
            msg = f"{path}: expected profile names to be strings"
            raise ConfigError(msg)
        profiles[name] = _profile_config(name, entry, path=path)
    return profiles


def _profile_config(name: str, entry: object, *, path: Path) -> ProfileConfig:
    if not isinstance(entry, CommentedMap):
        msg = f"{path}: expected profile '{name}' to be a mapping"
        raise ConfigError(msg)
    rules = _section(entry, "rules", path=path)
    checks = _section(entry, "checks", path=path)
    return ProfileConfig(
        description=_optional_string(entry, "description", path=path),
        excluded_rule_ids=_string_list(rules, "excluded_rule_ids", path=path),
        excluded_rule_tags=_string_list(rules, "excluded_tags", path=path),
        excluded_checks=_string_list(checks, "excluded", path=path),
        excluded_check_tags=_string_list(checks, "excluded_tags", path=path),
    )


def _write_profiles(data: CommentedMap, profiles: dict[str, ProfileConfig]) -> None:
    if not profiles:
        return
    rendered = CommentedMap()
    for name, profile in profiles.items():
        entry = CommentedMap()
        if profile.description is not None:
            entry["description"] = profile.description
        entry["rules"] = {
            "excluded_tags": list(profile.excluded_rule_tags),
            "excluded_rule_ids": list(profile.excluded_rule_ids),
        }
        entry["checks"] = {
            "excluded_tags": list(profile.excluded_check_tags),
            "excluded": list(profile.excluded_checks),
        }
        rendered[name] = entry
    data["profiles"] = rendered


def _optional_bool(section: CommentedMap, key: str, *, path: Path) -> bool | None:
    value = section.get(key)
    if value is None:
        return None
    if not isinstance(value, bool):
        msg = f"{path}: expected '{key}' to be a boolean"
        raise ConfigError(msg)
    return value


def _optional_positive_int(section: CommentedMap, key: str, *, path: Path) -> int | None:
    value = section.get(key)
    if value is None:
        return None
    # bool is an int subclass; reject it so `true`/`false` is not read as 1/0.
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        msg = f"{path}: expected '{key}' to be a positive integer or null"
        raise ConfigError(msg)
    return value


def _string_list(section: CommentedMap, key: str, *, path: Path) -> list[str]:
    value = section.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        msg = f"{path}: expected '{key}' to be a list of strings"
        raise ConfigError(msg)
    items: list[str] = []
    for item in value:
        if not isinstance(item, str):
            msg = f"{path}: expected '{key}' to be a list of strings"
            raise ConfigError(msg)
        items.append(item)
    return items
