"""Typed load/save for the four BYOR config schemas."""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from pathlib import Path

from ruamel.yaml.comments import CommentedMap

from byor.errors import ConfigError, RepoNotInitialized
from byor.io.yamlio import load_yaml_mapping, write_yaml_atomic

CONFIG_VERSION = 1


@dataclass
class RepoPaths:
    """POSIX-style paths relative to the repo root."""

    sgconfig: str = "sgconfig.yml"
    project_rules: str = ".byor/rules/project"
    personal_local_rules: str = ".byor/rules/personal/local"
    personal_global_rules: str = ".byor/rules/personal/global"


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


@dataclass
class RepoConfig:
    """Tracked repository config: .byor/config.yml.

    A repo carries only rule/check content; AI agents are registered globally
    (see GlobalConfig.agents), not per repo.
    """

    project_name: str | None = None
    paths: RepoPaths = field(default_factory=RepoPaths)
    checks: list[CheckDef] = field(default_factory=list)


@dataclass
class LocalConfig:
    """Untracked per-user repository config: .byor/local.yml."""

    excluded_rule_ids: list[str] = field(default_factory=list)
    excluded_checks: list[str] = field(default_factory=list)


@dataclass
class InitDefaults:
    """Global defaults for init's prompts and `--non-interactive` answers.

    `None` means "no global default; fall back to the built-in default"; an
    explicit init flag always wins over both.
    """

    ignore_mode: str | None = None
    git_hooks: bool | None = None


@dataclass
class GlobalConfig:
    """Global user config: <global dir>/config.yml.

    Paths are relative to the global config dir unless absolute.
    """

    rules_path: str = "rules"
    repos_path: str = "repos.yml"
    ast_grep_command: str = "auto"
    agents: list[str] = field(default_factory=list)
    """The AI agents registered globally by `byor install` / `byor hook install`."""
    checks: list[CheckDef] = field(default_factory=list)
    init: InitDefaults = field(default_factory=InitDefaults)


def rule_dir_relpaths(paths: RepoPaths) -> list[str]:
    """The three rule directories sgconfig.yml must list, repo-relative."""
    return [
        paths.project_rules,
        paths.personal_local_rules,
        paths.personal_global_rules,
    ]


def repo_config_path(repo_root: Path) -> Path:
    return repo_root / ".byor" / "config.yml"


def local_config_path(repo_root: Path) -> Path:
    return repo_root / ".byor" / "local.yml"


def global_config_path(config_dir: Path) -> Path:
    return config_dir / "config.yml"


def global_rules_dir(config_dir: Path, config: GlobalConfig) -> Path:
    return config_dir / config.rules_path


def repo_registry_path(config_dir: Path, config: GlobalConfig) -> Path:
    return config_dir / config.repos_path


def load_repo_config(repo_root: Path) -> RepoConfig:
    path = repo_config_path(repo_root)
    if not path.is_file():
        raise RepoNotInitialized(
            f"{repo_root} has no .byor/config.yml. Run `byor init` first."
        )
    data = load_yaml_mapping(path)
    _check_version(data, path)
    project = _section(data, "project", path)
    paths = _section(data, "paths", path)
    defaults = RepoPaths()
    return RepoConfig(
        project_name=_optional_string(project, "name", path),
        paths=RepoPaths(
            sgconfig=_string(paths, "sgconfig", defaults.sgconfig, path),
            project_rules=_string(paths, "project_rules", defaults.project_rules, path),
            personal_local_rules=_string(
                paths, "personal_local_rules", defaults.personal_local_rules, path
            ),
            personal_global_rules=_string(
                paths, "personal_global_rules", defaults.personal_global_rules, path
            ),
        ),
        checks=_check_defs(data, path),
    )


def save_repo_config(repo_root: Path, config: RepoConfig) -> None:
    path = repo_config_path(repo_root)
    data = _load_or_new(path)
    data["version"] = CONFIG_VERSION
    _update_section(data, "project", {"name": config.project_name})
    _update_section(
        data,
        "paths",
        {
            "sgconfig": config.paths.sgconfig,
            "project_rules": config.paths.project_rules,
            "personal_local_rules": config.paths.personal_local_rules,
            "personal_global_rules": config.paths.personal_global_rules,
        },
    )
    _write_checks(data, config.checks)
    write_yaml_atomic(path, data)


def load_local_config(repo_root: Path) -> LocalConfig:
    """Load .byor/local.yml, defaulting when absent (it is gitignored)."""
    path = local_config_path(repo_root)
    if not path.is_file():
        return LocalConfig()
    data = load_yaml_mapping(path)
    _check_version(data, path)
    section = _section(data, "global", path)
    checks = _section(data, "checks", path)
    return LocalConfig(
        excluded_rule_ids=_string_list(section, "excluded_rule_ids", path),
        excluded_checks=_string_list(checks, "excluded", path),
    )


def save_local_config(repo_root: Path, config: LocalConfig) -> None:
    path = local_config_path(repo_root)
    data = _load_or_new(path)
    data["version"] = CONFIG_VERSION
    _update_section(
        data, "global", {"excluded_rule_ids": list(config.excluded_rule_ids)}
    )
    _update_section(data, "checks", {"excluded": list(config.excluded_checks)})
    write_yaml_atomic(path, data)


def load_global_config(config_dir: Path) -> GlobalConfig:
    """Load <global dir>/config.yml, defaulting when absent (init creates it)."""
    path = global_config_path(config_dir)
    if not path.is_file():
        return GlobalConfig()
    data = load_yaml_mapping(path)
    _check_version(data, path)
    paths = _section(data, "paths", path)
    ast_grep = _section(data, "ast_grep", path)
    ai = _section(data, "ai", path)
    init = _section(data, "init", path)
    defaults = GlobalConfig()
    return GlobalConfig(
        rules_path=_string(paths, "rules", defaults.rules_path, path),
        repos_path=_string(paths, "repos", defaults.repos_path, path),
        ast_grep_command=_string(ast_grep, "command", defaults.ast_grep_command, path),
        agents=_string_list(ai, "agents", path),
        checks=_check_defs(data, path),
        init=InitDefaults(
            ignore_mode=_optional_string(init, "ignore_mode", path),
            git_hooks=_optional_bool(init, "git_hooks", path),
        ),
    )


def save_global_config(config_dir: Path, config: GlobalConfig) -> None:
    path = global_config_path(config_dir)
    data = _load_or_new(path)
    data["version"] = CONFIG_VERSION
    _update_section(
        data, "paths", {"rules": config.rules_path, "repos": config.repos_path}
    )
    _update_section(data, "ast_grep", {"command": config.ast_grep_command})
    _update_section(data, "ai", {"agents": list(config.agents)})
    _write_checks(data, config.checks)
    _write_init_defaults(data, config.init)
    write_yaml_atomic(path, data)


def load_repo_registry(path: Path) -> list[Path]:
    """Load the absolute repo roots registered for `sync --all`."""
    if not path.is_file():
        return []
    data = load_yaml_mapping(path)
    _check_version(data, path)
    return [Path(entry) for entry in _string_list(data, "repos", path)]


def save_repo_registry(path: Path, repos: list[Path]) -> None:
    data = _load_or_new(path)
    data["version"] = CONFIG_VERSION
    data["repos"] = [str(repo) for repo in repos]
    write_yaml_atomic(path, data)


def register_repo(repo_root: Path, registry_path: Path) -> bool:
    """Record repo_root in the global registry. Returns True when newly added."""
    repos = load_repo_registry(registry_path)
    resolved = repo_root.resolve()
    if resolved in repos:
        return False
    repos.append(resolved)
    save_repo_registry(registry_path, repos)
    return True


def _load_or_new(path: Path) -> CommentedMap:
    return load_yaml_mapping(path) if path.is_file() else CommentedMap()


def _update_section(
    data: CommentedMap, key: str, values: dict[str, str | None | bool | list[str]]
) -> None:
    """Set managed keys in place so user comments and unknown keys survive."""
    section = data.get(key)
    if not isinstance(section, CommentedMap):
        section = CommentedMap()
        data[key] = section
    for name, value in values.items():
        section[name] = value


def _check_version(data: CommentedMap, path: Path) -> None:
    if data.get("version") != CONFIG_VERSION:
        raise ConfigError(f"{path}: expected `version: {CONFIG_VERSION}`")


def _section(data: CommentedMap, key: str, path: Path) -> CommentedMap:
    value = data.get(key)
    if value is None:
        return CommentedMap()
    if not isinstance(value, CommentedMap):
        raise ConfigError(f"{path}: expected '{key}' to be a mapping")
    return value


def _string(section: CommentedMap, key: str, default: str, path: Path) -> str:
    value = section.get(key, default)
    if not isinstance(value, str):
        raise ConfigError(f"{path}: expected '{key}' to be a string")
    return value


def _optional_string(section: CommentedMap, key: str, path: Path) -> str | None:
    value = section.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ConfigError(f"{path}: expected '{key}' to be a string or null")
    return value


def _check_defs(data: CommentedMap, path: Path) -> list[CheckDef]:
    """Parse the `checks:` list shared by repo and global config."""
    value = data.get("checks")
    if value is None:
        return []
    if not isinstance(value, list):
        raise ConfigError(f"{path}: expected 'checks' to be a list of mappings")
    return [_check_def(entry, path) for entry in value]


def _check_def(entry: object, path: Path) -> CheckDef:
    if not isinstance(entry, CommentedMap):
        raise ConfigError(f"{path}: expected each check to be a mapping")
    name = _string(entry, "name", "", path)
    run = _string(entry, "run", "", path)
    if not name or not run:
        raise ConfigError(f"{path}: each check needs a non-empty 'name' and 'run'")
    try:
        argv = shlex.split(run)
    except ValueError as error:
        raise ConfigError(
            f"{path}: check '{name}' has an invalid 'run': {error}"
        ) from error
    if not argv:
        raise ConfigError(f"{path}: check '{name}' has an empty 'run' command")
    return CheckDef(
        name=name,
        extensions=_string_list(entry, "extensions", path),
        run=run,
    )


def _write_checks(data: CommentedMap, checks: list[CheckDef]) -> None:
    data["checks"] = [
        {"name": check.name, "extensions": list(check.extensions), "run": check.run}
        for check in checks
    ]


def _write_init_defaults(data: CommentedMap, init: InitDefaults) -> None:
    values: dict[str, str | None | bool | list[str]] = {}
    if init.ignore_mode is not None:
        values["ignore_mode"] = init.ignore_mode
    if init.git_hooks is not None:
        values["git_hooks"] = init.git_hooks
    if values:
        _update_section(data, "init", values)


def _optional_bool(section: CommentedMap, key: str, path: Path) -> bool | None:
    value = section.get(key)
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ConfigError(f"{path}: expected '{key}' to be a boolean")
    return value


def _string_list(section: CommentedMap, key: str, path: Path) -> list[str]:
    value = section.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        raise ConfigError(f"{path}: expected '{key}' to be a list of strings")
    items: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ConfigError(f"{path}: expected '{key}' to be a list of strings")
        items.append(item)
    return items
