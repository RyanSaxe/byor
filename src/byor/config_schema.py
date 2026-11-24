"""Parse, validate, and serialize BYOR YAML configuration sections.

Configuration files across repository, local, global, package, and state layers share strict YAML
validation and round-trip serialization primitives. Keeping the shared dataclasses and typed field
codecs here keeps `byor.config` focused on layer orchestration and machine-state management without
duplicating schema validation logic.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from ruamel.yaml.comments import CommentedMap

from byor.errors import ConfigError

__all__ = (
    "CONFIG_VERSION",
    "CheckDef",
    "CommandCheckDef",
    "InitDefaults",
    "ProfileConfig",
    "SectionValue",
    "bool_field",
    "check_defs",
    "check_version",
    "command_check_defs",
    "fail_on",
    "optional_bool",
    "optional_positive_int",
    "optional_string",
    "package_names",
    "profile_configs",
    "section",
    "string",
    "string_list",
    "update_section",
    "write_checks",
    "write_command_checks",
    "write_init_defaults",
    "write_profiles",
)

CONFIG_VERSION = 1

# The value types a managed config section may hold, written back in place.
SectionValue = str | None | bool | int | list[str]


@dataclass(frozen=True)
class CheckDef:
    """An extra command-line check run after ast-grep.

    `run` is shlex-split into argv and run without a shell; the in-scope files
    are appended as trailing arguments, so the command must accept a list of
    file paths. A leading `~`/`~/` in the command expands to the user's home.
    `extensions` (no dots) filters which files trigger the check. `gate: false`
    keeps a check out of the generated pre-commit and CI gate while the agent
    hook still runs it — for checks that police agents, not humans.
    """

    name: str
    extensions: list[str]
    run: str
    tags: list[str] = field(default_factory=list)
    gate: bool = True


@dataclass(frozen=True)
class CommandCheckDef:
    """A script veto for the pre-command gate.

    `run` is shlex-split into argv and run without a shell; the candidate shell
    command is written to the process's stdin. A nonzero exit denies the
    command and the check's output becomes the corrective message. These run
    on every shell command an agent issues, so they must be fast, and they
    never appear in the generated pre-commit or CI gate.
    """

    name: str
    run: str
    tags: list[str] = field(default_factory=list)


@dataclass
class InitDefaults:
    private: bool | None = None
    git_hooks: bool | None = None
    gate: bool | None = None
    # `profile` predates `profiles`; both are honored and merged at init.
    profile: str | None = None
    profiles: list[str] = field(default_factory=list)
    package: str | None = None
    packages: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ProfileConfig:
    description: str | None = None
    excluded_rule_ids: list[str] = field(default_factory=list)
    excluded_rule_tags: list[str] = field(default_factory=list)
    excluded_checks: list[str] = field(default_factory=list)
    excluded_check_tags: list[str] = field(default_factory=list)


def update_section(data: CommentedMap, key: str, *, values: dict[str, SectionValue]) -> None:
    target = data.get(key)
    if not isinstance(target, CommentedMap):
        target = CommentedMap()
        data[key] = target
    for name, value in values.items():
        target[name] = value


def check_version(data: CommentedMap, path: Path) -> None:
    if data.get("version") != CONFIG_VERSION:
        msg = f"{path}: expected `version: {CONFIG_VERSION}`"
        raise ConfigError(msg)


def section(data: CommentedMap, key: str, *, path: Path) -> CommentedMap:
    value = data.get(key)
    if value is None:
        return CommentedMap()
    if not isinstance(value, CommentedMap):
        msg = f"{path}: expected '{key}' to be a mapping"
        raise ConfigError(msg)
    return value


def string(mapping: CommentedMap, key: str, *, default: str, path: Path) -> str:
    value = mapping.get(key, default)
    if not isinstance(value, str):
        msg = f"{path}: expected '{key}' to be a string"
        raise ConfigError(msg)
    return value


def bool_field(mapping: CommentedMap, key: str, *, path: Path, default: bool) -> bool:
    value = mapping.get(key, default)
    if not isinstance(value, bool):
        msg = f"{path}: expected '{key}' to be a boolean"
        raise ConfigError(msg)
    return value


def fail_on(data: CommentedMap, path: Path) -> Literal["all", "error"]:
    value = string(data, "fail_on", default="all", path=path)
    if value == "all":
        return "all"
    if value == "error":
        return "error"
    msg = f"{path}: expected 'fail_on' to be 'all' or 'error'"
    raise ConfigError(msg)


def optional_string(mapping: CommentedMap, key: str, *, path: Path) -> str | None:
    value = mapping.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        msg = f"{path}: expected '{key}' to be a string or null"
        raise ConfigError(msg)
    return value


def check_defs(data: CommentedMap, path: Path) -> list[CheckDef]:
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
    name = string(entry, "name", default="", path=path)
    run = string(entry, "run", default="", path=path)
    if not name or not run:
        msg = f"{path}: each check needs a non-empty 'name' and 'run'"
        raise ConfigError(msg)
    _require_splittable_run(name, run, path=path)
    return CheckDef(
        name=name,
        extensions=string_list(entry, "extensions", path=path),
        run=run,
        tags=string_list(entry, "tags", path=path),
        gate=bool_field(entry, "gate", path=path, default=True),
    )


def _require_splittable_run(name: str, run: str, *, path: Path) -> None:
    try:
        argv = shlex.split(run)
    except ValueError as error:
        msg = f"{path}: check '{name}' has an invalid 'run': {error}"
        raise ConfigError(msg) from error
    if not argv:
        msg = f"{path}: check '{name}' has an empty 'run' command"
        raise ConfigError(msg)


def command_check_defs(data: CommentedMap, path: Path) -> list[CommandCheckDef]:
    value = data.get("command_checks")
    if value is None:
        return []
    if not isinstance(value, list):
        msg = f"{path}: expected 'command_checks' to be a list of mappings"
        raise ConfigError(msg)
    return [_command_check_def(entry, path) for entry in value]


def _command_check_def(entry: object, path: Path) -> CommandCheckDef:
    if not isinstance(entry, CommentedMap):
        msg = f"{path}: expected each command check to be a mapping"
        raise ConfigError(msg)
    name = string(entry, "name", default="", path=path)
    run = string(entry, "run", default="", path=path)
    if not name or not run:
        msg = f"{path}: each command check needs a non-empty 'name' and 'run'"
        raise ConfigError(msg)
    _require_splittable_run(name, run, path=path)
    return CommandCheckDef(name=name, run=run, tags=string_list(entry, "tags", path=path))


def write_command_checks(data: CommentedMap, checks: list[CommandCheckDef]) -> None:
    # Unlike `checks`, this key is omitted when empty: most configs will never
    # gate commands with scripts, so they should not grow a `command_checks: []`.
    if not checks:
        data.pop("command_checks", None)
        return
    rendered = []
    for check in checks:
        entry: dict[str, str | list[str]] = {"name": check.name, "run": check.run}
        if check.tags:
            entry["tags"] = list(check.tags)
        rendered.append(entry)
    data["command_checks"] = rendered


def write_checks(data: CommentedMap, checks: list[CheckDef]) -> None:
    rendered = []
    for check in checks:
        entry: dict[str, str | list[str] | bool] = {
            "name": check.name,
            "extensions": list(check.extensions),
            "run": check.run,
        }
        if check.tags:
            entry["tags"] = list(check.tags)
        if not check.gate:
            entry["gate"] = False
        rendered.append(entry)
    data["checks"] = rendered


def write_init_defaults(data: CommentedMap, init: InitDefaults) -> None:
    entries: tuple[tuple[str, SectionValue | None], ...] = (
        ("private", init.private),
        ("git_hooks", init.git_hooks),
        ("gate", init.gate),
        ("profile", init.profile),
        ("profiles", list(init.profiles) if init.profiles else None),
        ("package", init.package),
        ("packages", list(init.packages) if init.packages else None),
    )
    values: dict[str, SectionValue] = {key: value for key, value in entries if value is not None}
    if values:
        update_section(data, "init", values=values)


def profile_configs(data: CommentedMap, path: Path) -> dict[str, ProfileConfig]:
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
    rules = section(entry, "rules", path=path)
    checks = section(entry, "checks", path=path)
    return ProfileConfig(
        description=optional_string(entry, "description", path=path),
        excluded_rule_ids=string_list(rules, "excluded_rule_ids", path=path),
        excluded_rule_tags=string_list(rules, "excluded_tags", path=path),
        excluded_checks=string_list(checks, "excluded", path=path),
        excluded_check_tags=string_list(checks, "excluded_tags", path=path),
    )


def write_profiles(data: CommentedMap, profiles: dict[str, ProfileConfig]) -> None:
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


def optional_bool(mapping: CommentedMap, key: str, *, path: Path) -> bool | None:
    value = mapping.get(key)
    if value is None:
        return None
    if not isinstance(value, bool):
        msg = f"{path}: expected '{key}' to be a boolean"
        raise ConfigError(msg)
    return value


def optional_positive_int(mapping: CommentedMap, key: str, *, path: Path) -> int | None:
    value = mapping.get(key)
    if value is None:
        return None
    # bool is an int subclass; reject it so `true`/`false` is not read as 1/0.
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        msg = f"{path}: expected '{key}' to be a positive integer or null"
        raise ConfigError(msg)
    return value


def package_names(mapping: CommentedMap, *, path: Path) -> list[str]:
    # Package names are joined onto the global packages dir and the repo's
    # packages mirror, so a path-like name would escape both. local.yml is
    # user-owned and hand-editable: validate at load, not just in `package add`.
    names = string_list(mapping, "packages", path=path)
    for name in names:
        if name in ("", ".", "..") or "\\" in name or name != Path(name).name:
            msg = f"{path}: package name '{name}' must be a bare directory name, with no path separators or '..'"
            raise ConfigError(msg)
    return names


def string_list(mapping: CommentedMap, key: str, *, path: Path) -> list[str]:
    value = mapping.get(key)
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
