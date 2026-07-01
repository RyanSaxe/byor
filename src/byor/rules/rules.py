"""Load and validate ast-grep rule files.

Rule files carry ast-grep YAML plus BYOR metadata used by listing and agent feedback. This module
owns discovery, parsing, ID validation, conflict detection, and safe scope resolution for every
command that touches rules.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from ruamel.yaml.comments import CommentedMap

from byor.errors import ConfigError, DuplicateRuleIdError, RuleValidationError
from byor.io.yamlio import parse_yaml_mapping

if TYPE_CHECKING:
    from collections.abc import Iterable

    from byor.config import RepoPaths

__all__ = (
    "ByorMetadata",
    "Rule",
    "check_id_conflicts",
    "discover_rule_files",
    "load_rule",
    "load_rules",
    "require_unique_ids",
    "rule_id_warnings",
    "scope_rules_dir",
)

RuleScope = Literal["project", "local", "global"]

RULE_FILE_SUFFIXES = (".yml", ".yaml")
REQUIRED_AST_GREP_FIELDS = ("id", "language", "rule", "message")
RECOMMENDED_ID_PATTERN = re.compile(r"[a-z][a-z0-9-]*(\.[a-z][a-z0-9-]*)*")

# The one place the suppression-comment syntax is spelled out;
# every instruction string interpolates it.
SUPPRESSION_COMMENT = "# ast-grep-ignore: <rule-id>"

# The standard exception sentence an agent_prompt ends with when a rule
# tolerates exceptions. `byor add --allow-exceptions` appends it;
# the capture skill includes it when the user allows exceptions.
ALLOW_EXCEPTIONS_SENTENCE = (
    f"If this is genuinely necessary, add `{SUPPRESSION_COMMENT}` at the end of "
    "the offending line, with a short comment on the line above explaining why."
)


@dataclass
class ByorMetadata:
    rationale: str | None = None
    agent_prompt: str | None = None
    docs_url: str | None = None
    tags: list[str] = field(default_factory=list)


@dataclass
class Rule:
    id: str
    language: str
    message: str
    path: Path

    content: str
    """The raw file text, so sync can mirror the rule without rereading it."""

    severity: str | None = None
    byor: ByorMetadata = field(default_factory=ByorMetadata)

    @property
    def tags(self) -> list[str]:
        return self.byor.tags


def discover_rule_files(rules_dir: Path) -> list[Path]:
    if not rules_dir.is_dir():
        return []
    return sorted(path for path in rules_dir.rglob("*") if path.suffix in RULE_FILE_SUFFIXES and path.is_file())


def load_rule(path: Path) -> Rule:
    """Minimally parse one rule file; every failure message names the file.

    Strict only about the fields ast-grep requires. The optional
    metadata.byor block degrades to defaults when malformed: ast-grep
    ignores metadata, and deep validation is doctor's job.
    """
    text = path.read_text(encoding="utf-8")
    try:
        data = parse_yaml_mapping(text, source=path)
    except ConfigError as error:
        raise RuleValidationError(str(error)) from error
    missing = [name for name in REQUIRED_AST_GREP_FIELDS if data.get(name) is None]
    if missing:
        msg = f"{path}: missing required ast-grep fields: {', '.join(missing)}"
        raise RuleValidationError(msg)
    if not isinstance(data.get("rule"), CommentedMap):
        msg = f"{path}: expected 'rule' to be a mapping"
        raise RuleValidationError(msg)
    return Rule(
        id=_safe_rule_id(_string(data, "id", path=path), path),
        language=_string(data, "language", path=path),
        message=_string(data, "message", path=path),
        path=path,
        content=text,
        severity=_lenient_string(data, "severity"),
        byor=_byor_metadata(data),
    )


def load_rules(rules_dir: Path) -> list[Rule]:
    return [load_rule(path) for path in discover_rule_files(rules_dir)]


def _safe_rule_id(rule_id: str, path: Path) -> str:
    if rule_id in ("", ".", "..") or "\\" in rule_id or rule_id != Path(rule_id).name:
        msg = f"{path}: rule ID '{rule_id}' must be a bare name, with no path separators or '..' components"
        raise RuleValidationError(msg)
    return rule_id


def rule_id_warnings(rules: Iterable[Rule]) -> list[str]:
    return [
        f"{rule.path}: rule ID '{rule.id}' does not match the recommended pattern {RECOMMENDED_ID_PATTERN.pattern}"
        for rule in rules
        if not RECOMMENDED_ID_PATTERN.fullmatch(rule.id)
    ]


def scope_rules_dir(scope: RuleScope, repo_root: Path, *, paths: RepoPaths, global_rules_root: Path) -> Path:
    if scope == "project":
        return repo_root / paths.project_rules
    if scope == "local":
        return repo_root / paths.personal_local_rules
    return global_rules_root


def check_id_conflicts(project: list[Rule], local: list[Rule], *, canonical_global: list[Rule]) -> None:
    """Enforce the rule-ID conflict table.

    Duplicate IDs within one scope and project/local collisions raise
    DuplicateRuleIdError. Project or local IDs matching global IDs are overrides,
    not errors: sync skips the global copy.
    """
    require_unique_ids(project, "project rules")
    require_unique_ids(local, "local personal rules")
    require_unique_ids(canonical_global, "canonical global rules")
    # Each scope is unique on its own, so any duplicate here is cross-scope.
    require_unique_ids(
        project + local,
        "project and local personal rules combined",
        hint="A local variation of a project rule requires a different ID.",
    )


def require_unique_ids(rules: list[Rule], where: str, *, hint: str | None = None) -> None:
    paths_by_id: dict[str, list[Path]] = {}
    for rule in rules:
        paths_by_id.setdefault(rule.id, []).append(rule.path)
    duplicates = {rule_id: paths for rule_id, paths in paths_by_id.items() if len(paths) > 1}
    if not duplicates:
        return
    lines = [f"Duplicate rule IDs within {where}:"]
    for rule_id, paths in sorted(duplicates.items()):
        lines.append(f"  {rule_id}:")
        lines.extend(f"    {path}" for path in paths)
    if hint is not None:
        lines.append(hint)
    raise DuplicateRuleIdError("\n".join(lines))


def _byor_metadata(data: CommentedMap) -> ByorMetadata:
    metadata = data.get("metadata")
    block = metadata.get("byor") if isinstance(metadata, CommentedMap) else None
    if not isinstance(block, CommentedMap):
        return ByorMetadata()
    return ByorMetadata(
        rationale=_lenient_string(block, "rationale"),
        agent_prompt=_lenient_string(block, "agent_prompt"),
        docs_url=_lenient_string(block, "docs_url"),
        tags=_lenient_string_list(block, "tags"),
    )


def _string(section: CommentedMap, key: str, *, path: Path) -> str:
    value = section.get(key)
    if not isinstance(value, str):
        msg = f"{path}: expected '{key}' to be a string"
        raise RuleValidationError(msg)
    return value


def _lenient_string(section: CommentedMap, key: str) -> str | None:
    value = section.get(key)
    return value if isinstance(value, str) else None


def _lenient_string_list(section: CommentedMap, key: str) -> list[str]:
    value = section.get(key)
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]
