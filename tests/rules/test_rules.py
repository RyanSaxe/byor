"""Exercise ast-grep rule loading and validation.

The loader discovers YAML files recursively in sorted order and parses byor metadata leniently:
optional fields default, malformed metadata degrades rather than fails, and errors name the
offending file. Duplicate-id semantics carry the sync model — duplicates within one scope are
errors, project or local over global is an override, but project versus local is a conflict.
"""

from pathlib import Path

import pytest
from support import write_rule

from byor.config import RepoPaths
from byor.errors import DuplicateRuleIdError, RuleValidationError
from byor.rules.rules import (
    Rule,
    check_id_conflicts,
    discover_rule_files,
    load_rule,
    load_rules,
    rule_id_warnings,
    scope_rules_dir,
)

NO_PYTHON_CAST = """\
id: no-python-cast
language: Python
severity: warning
message: Avoid typing.cast in Python code.
rule:
  pattern: cast($TYPE, $VALUE)
metadata:
  byor:
    rationale: casting hides type model problems.
    agent_prompt: Do not use typing.cast here.
    docs_url: https://example.com/no-python-cast
    tags:
      - python
      - typing
"""


def test_discovery_finds_yaml_files_recursively_and_sorted(tmp_path: Path) -> None:
    write_rule(tmp_path / "python" / "b.yml", "b")
    write_rule(tmp_path / "a.yaml", "a")
    (tmp_path / ".gitkeep").touch()
    (tmp_path / "notes.md").write_text("not a rule\n")

    assert discover_rule_files(tmp_path) == [
        tmp_path / "a.yaml",
        tmp_path / "python" / "b.yml",
    ]


def test_discovery_of_missing_directory_is_empty(tmp_path: Path) -> None:
    assert discover_rule_files(tmp_path / "absent") == []


def test_load_rule_parses_byor_metadata(tmp_path: Path) -> None:
    path = tmp_path / "no-python-cast.yml"
    path.write_text(NO_PYTHON_CAST)

    rule = load_rule(path)

    assert rule.id == "no-python-cast"
    assert rule.language == "Python"
    assert rule.message == "Avoid typing.cast in Python code."
    assert rule.path == path
    assert rule.tags == ["python", "typing"]


def test_load_rule_defaults_optional_fields(tmp_path: Path) -> None:
    rule = load_rule(write_rule(tmp_path / "minimal.yml", "minimal"))

    assert rule.tags == []


def test_load_rule_names_file_when_required_fields_missing(tmp_path: Path) -> None:
    path = tmp_path / "broken.yml"
    path.write_text("id: lonely\nseverity: warning\n")

    with pytest.raises(RuleValidationError, match=r"broken\.yml.*language, rule"):
        load_rule(path)


def test_load_rule_names_file_on_invalid_yaml(tmp_path: Path) -> None:
    path = tmp_path / "invalid.yml"
    path.write_text("id: [unclosed\n")

    with pytest.raises(RuleValidationError, match=r"invalid\.yml"):
        load_rule(path)


def test_load_rule_degrades_malformed_metadata_to_defaults(tmp_path: Path) -> None:
    path = tmp_path / "odd-metadata.yml"
    path.write_text(
        "id: odd-metadata\n"
        "language: Python\n"
        "message: Avoid this pattern.\n"
        "rule:\n"
        "  pattern: cast($TYPE, $VALUE)\n"
        "metadata:\n"
        "  byor:\n"
        "    tags: python\n"
    )

    rule = load_rule(path)

    assert rule.tags == []


def test_load_rules_parses_every_discovered_file(tmp_path: Path) -> None:
    write_rule(tmp_path / "a.yml", "a")
    write_rule(tmp_path / "python" / "b.yml", "b")

    assert [rule.id for rule in load_rules(tmp_path)] == ["a", "b"]


def test_unconventional_rule_id_warns_but_loads(tmp_path: Path) -> None:
    good = load_rule(write_rule(tmp_path / "good.yml", "python.no-cast"))
    odd = load_rule(write_rule(tmp_path / "odd.yml", "No_Cast"))

    assert rule_id_warnings([good]) == []
    warnings = rule_id_warnings([good, odd])
    assert len(warnings) == 1
    assert "odd.yml" in warnings[0]
    assert "'No_Cast'" in warnings[0]


# One test per conflict table row.


def test_duplicate_id_within_project_rules_is_an_error() -> None:
    duplicated = [
        Rule("no-cast", "Python", "msg", Path("a.yml"), ""),
        Rule("no-cast", "Python", "msg", Path("b.yml"), ""),
    ]

    with pytest.raises(DuplicateRuleIdError, match=r"(?s)project rules.*a\.yml.*b\.yml"):
        check_id_conflicts(duplicated, [], canonical_global=[])


def test_duplicate_id_within_local_rules_is_an_error() -> None:
    duplicated = [
        Rule("no-cast", "Python", "msg", Path("a.yml"), ""),
        Rule("no-cast", "Python", "msg", Path("b.yml"), ""),
    ]

    with pytest.raises(DuplicateRuleIdError, match="local personal rules"):
        check_id_conflicts([], duplicated, canonical_global=[])


def test_duplicate_id_within_canonical_global_rules_is_an_error() -> None:
    duplicated = [
        Rule("no-cast", "Python", "msg", Path("a.yml"), ""),
        Rule("no-cast", "Python", "msg", Path("b.yml"), ""),
    ]

    with pytest.raises(DuplicateRuleIdError, match="global rules"):
        check_id_conflicts([], [], canonical_global=duplicated)


def test_project_id_matching_global_id_is_an_override_not_an_error() -> None:
    project = Rule("no-cast", "Python", "msg", Path("project.yml"), "")
    global_rule = Rule("no-cast", "Python", "msg", Path("global.yml"), "")

    assert check_id_conflicts([project], [], canonical_global=[global_rule]) is None


def test_local_id_matching_global_id_is_an_override_not_an_error() -> None:
    local = Rule("no-cast", "Python", "msg", Path("local.yml"), "")
    global_rule = Rule("no-cast", "Python", "msg", Path("global.yml"), "")

    assert check_id_conflicts([], [local], canonical_global=[global_rule]) is None


def test_project_id_matching_local_id_is_an_error() -> None:
    project = Rule("no-cast", "Python", "msg", Path("project.yml"), "")
    local = Rule("no-cast", "Python", "msg", Path("local.yml"), "")

    with pytest.raises(DuplicateRuleIdError, match="requires a different ID"):
        check_id_conflicts([project], [local], canonical_global=[])


def test_scope_rules_dirs_map_to_repo_paths_and_canonical_global_root() -> None:
    repo_root = Path("/repo")
    global_root = Path("/home/user/.config/byor/rules")
    paths = RepoPaths()

    assert (
        scope_rules_dir("project", repo_root, paths=paths, global_rules_root=global_root)
        == repo_root / ".byor/rules/project"
    )
    assert (
        scope_rules_dir("local", repo_root, paths=paths, global_rules_root=global_root)
        == repo_root / ".byor/rules/personal/local"
    )
    assert scope_rules_dir("global", repo_root, paths=paths, global_rules_root=global_root) == global_root
