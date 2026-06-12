"""Rule discovery, minimal parsing, and ID validation (SPEC sections 11, 12, 14)."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from ruamel.yaml.comments import CommentedMap

from byolsp.config import RepoPaths
from byolsp.errors import DuplicateRuleId, RuleValidationError
from byolsp.yamlio import load_yaml_mapping

RuleScope = Literal["project", "local", "global"]
RULE_SCOPES: tuple[RuleScope, ...] = ("project", "local", "global")

RULE_FILE_SUFFIXES = (".yml", ".yaml")
REQUIRED_AST_GREP_FIELDS = ("id", "language", "rule", "message")
RECOMMENDED_ID_PATTERN = re.compile(r"[a-z][a-z0-9-]*(\.[a-z][a-z0-9-]*)*")


@dataclass
class ByolspMetadata:
    """The optional metadata.byolsp block of a rule file."""

    rationale: str | None = None
    agent_prompt: str | None = None
    allow_with_comment: bool = False
    docs_url: str | None = None
    tags: list[str] = field(default_factory=list)


@dataclass
class Rule:
    """One rule file, parsed just enough for sync, listing, and validation."""

    id: str
    language: str
    message: str
    path: Path
    severity: str | None = None
    byolsp: ByolspMetadata = field(default_factory=ByolspMetadata)


def discover_rule_files(rules_dir: Path) -> list[Path]:
    """All .yml/.yaml files below rules_dir, recursive, sorted. Missing dir is empty."""
    if not rules_dir.is_dir():
        return []
    return sorted(
        path
        for path in rules_dir.rglob("*")
        if path.suffix in RULE_FILE_SUFFIXES and path.is_file()
    )


def load_rule(path: Path) -> Rule:
    """Minimally parse one rule file; every failure message names the file."""
    data = load_yaml_mapping(path)
    missing = [name for name in REQUIRED_AST_GREP_FIELDS if data.get(name) is None]
    if missing:
        raise RuleValidationError(
            f"{path}: missing required ast-grep fields: {', '.join(missing)}"
        )
    if not isinstance(data.get("rule"), CommentedMap):
        raise RuleValidationError(f"{path}: expected 'rule' to be a mapping")
    return Rule(
        id=_string(data, "id", path),
        language=_string(data, "language", path),
        message=_string(data, "message", path),
        path=path,
        severity=_optional_string(data, "severity", path),
        byolsp=_byolsp_metadata(data, path),
    )


def load_rules(rules_dir: Path) -> list[Rule]:
    """Discover and parse every rule below rules_dir."""
    return [load_rule(path) for path in discover_rule_files(rules_dir)]


def rule_id_warnings(rules: Iterable[Rule]) -> list[str]:
    """Warnings for IDs outside the recommended pattern (SPEC 11.2); never rejects."""
    return [
        f"{rule.path}: rule ID '{rule.id}' does not match the recommended"
        f" pattern {RECOMMENDED_ID_PATTERN.pattern}"
        for rule in rules
        if not RECOMMENDED_ID_PATTERN.fullmatch(rule.id)
    ]


def scope_rules_dir(
    scope: RuleScope, repo_root: Path, paths: RepoPaths, global_rules_root: Path
) -> Path:
    """The directory a scope's rules live in.

    For the global scope this is the canonical global rules root, never the
    generated repo copy under personal/global (SPEC 12.3).
    """
    if scope == "project":
        return repo_root / paths.project_rules
    if scope == "local":
        return repo_root / paths.personal_local_rules
    return global_rules_root


def check_id_conflicts(
    project: list[Rule], local: list[Rule], canonical_global: list[Rule]
) -> None:
    """Enforce the SPEC section 14 conflict table.

    Duplicate IDs within one scope and project/local collisions raise
    DuplicateRuleId. Project or local IDs matching global IDs are overrides,
    not errors: sync skips the global copy.
    """
    _require_unique_ids(project, "project rules")
    _require_unique_ids(local, "local personal rules")
    _require_unique_ids(canonical_global, "canonical global rules")
    _require_project_local_disjoint(project, local)


def _require_unique_ids(rules: list[Rule], where: str) -> None:
    paths_by_id: dict[str, list[Path]] = {}
    for rule in rules:
        paths_by_id.setdefault(rule.id, []).append(rule.path)
    duplicates = {
        rule_id: paths for rule_id, paths in paths_by_id.items() if len(paths) > 1
    }
    if not duplicates:
        return
    lines = [f"Duplicate rule IDs within {where}:"]
    for rule_id, paths in sorted(duplicates.items()):
        lines.append(f"  {rule_id}:")
        lines.extend(f"    {path}" for path in paths)
    raise DuplicateRuleId("\n".join(lines))


def _require_project_local_disjoint(project: list[Rule], local: list[Rule]) -> None:
    local_by_id = {rule.id: rule for rule in local}
    conflicts = [
        (rule, local_by_id[rule.id]) for rule in project if rule.id in local_by_id
    ]
    if not conflicts:
        return
    lines = ["Rule IDs exist in both project and local personal rules:"]
    for project_rule, local_rule in conflicts:
        lines.append(f"  {project_rule.id}:")
        lines.append(f"    {project_rule.path}")
        lines.append(f"    {local_rule.path}")
    lines.append("A local variation of a project rule requires a different ID.")
    raise DuplicateRuleId("\n".join(lines))


def _byolsp_metadata(data: CommentedMap, path: Path) -> ByolspMetadata:
    metadata = data.get("metadata")
    if metadata is None:
        return ByolspMetadata()
    if not isinstance(metadata, CommentedMap):
        raise RuleValidationError(f"{path}: expected 'metadata' to be a mapping")
    block = metadata.get("byolsp")
    if block is None:
        return ByolspMetadata()
    if not isinstance(block, CommentedMap):
        raise RuleValidationError(f"{path}: expected 'metadata.byolsp' to be a mapping")
    return ByolspMetadata(
        rationale=_optional_string(block, "rationale", path),
        agent_prompt=_optional_string(block, "agent_prompt", path),
        allow_with_comment=_bool(block, "allow_with_comment", path),
        docs_url=_optional_string(block, "docs_url", path),
        tags=_string_list(block, "tags", path),
    )


def _string(section: CommentedMap, key: str, path: Path) -> str:
    value = section.get(key)
    if not isinstance(value, str):
        raise RuleValidationError(f"{path}: expected '{key}' to be a string")
    return value


def _optional_string(section: CommentedMap, key: str, path: Path) -> str | None:
    value = section.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise RuleValidationError(f"{path}: expected '{key}' to be a string")
    return value


def _bool(section: CommentedMap, key: str, path: Path) -> bool:
    value = section.get(key, False)
    if not isinstance(value, bool):
        raise RuleValidationError(f"{path}: expected '{key}' to be a boolean")
    return value


def _string_list(section: CommentedMap, key: str, path: Path) -> list[str]:
    value = section.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        raise RuleValidationError(f"{path}: expected '{key}' to be a list of strings")
    items: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise RuleValidationError(
                f"{path}: expected '{key}' to be a list of strings"
            )
        items.append(item)
    return items
