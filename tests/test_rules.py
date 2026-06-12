from pathlib import Path

import pytest

from byolsp.config import RepoPaths
from byolsp.errors import DuplicateRuleId, RuleValidationError
from byolsp.rules import (
    ByolspMetadata,
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
  byolsp:
    rationale: casting hides type model problems.
    agent_prompt: Do not use typing.cast here.
    allow_with_comment: true
    docs_url: https://example.com/no-python-cast
    tags:
      - python
      - typing
"""


def write_rule(path: Path, rule_id: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"id: {rule_id}\n"
        "language: Python\n"
        "message: Avoid this pattern.\n"
        "rule:\n"
        "  pattern: cast($TYPE, $VALUE)\n"
    )
    return path


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


def test_load_rule_parses_byolsp_metadata(tmp_path: Path) -> None:
    path = tmp_path / "no-python-cast.yml"
    path.write_text(NO_PYTHON_CAST)

    rule = load_rule(path)

    assert rule.id == "no-python-cast"
    assert rule.language == "Python"
    assert rule.severity == "warning"
    assert rule.message == "Avoid typing.cast in Python code."
    assert rule.path == path
    assert rule.byolsp == ByolspMetadata(
        rationale="casting hides type model problems.",
        agent_prompt="Do not use typing.cast here.",
        allow_with_comment=True,
        docs_url="https://example.com/no-python-cast",
        tags=["python", "typing"],
    )


def test_load_rule_defaults_optional_fields(tmp_path: Path) -> None:
    rule = load_rule(write_rule(tmp_path / "minimal.yml", "minimal"))

    assert rule.severity is None
    assert rule.byolsp == ByolspMetadata()


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
    """ast-grep ignores metadata, so a metadata typo must not break loading."""
    path = tmp_path / "odd-metadata.yml"
    path.write_text(
        "id: odd-metadata\n"
        "language: Python\n"
        "message: Avoid this pattern.\n"
        "rule:\n"
        "  pattern: cast($TYPE, $VALUE)\n"
        "metadata:\n"
        "  byolsp:\n"
        "    agent_prompt: Keep this prompt.\n"
        "    allow_with_comment: sometimes\n"
        "    tags: python\n"
    )

    rule = load_rule(path)

    assert rule.byolsp == ByolspMetadata(agent_prompt="Keep this prompt.")


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


def rule(rule_id: str, filename: str) -> Rule:
    return Rule(
        id=rule_id, language="Python", message="msg", path=Path(filename), content=""
    )


# One test per SPEC section 14 conflict table row.


def test_duplicate_id_within_project_rules_is_an_error() -> None:
    duplicated = [rule("no-cast", "a.yml"), rule("no-cast", "b.yml")]

    with pytest.raises(DuplicateRuleId, match=r"(?s)project rules.*a\.yml.*b\.yml"):
        check_id_conflicts(duplicated, [], [])


def test_duplicate_id_within_local_rules_is_an_error() -> None:
    duplicated = [rule("no-cast", "a.yml"), rule("no-cast", "b.yml")]

    with pytest.raises(DuplicateRuleId, match="local personal rules"):
        check_id_conflicts([], duplicated, [])


def test_duplicate_id_within_canonical_global_rules_is_an_error() -> None:
    duplicated = [rule("no-cast", "a.yml"), rule("no-cast", "b.yml")]

    with pytest.raises(DuplicateRuleId, match="global rules"):
        check_id_conflicts([], [], duplicated)


def test_project_id_matching_global_id_is_an_override_not_an_error() -> None:
    check_id_conflicts(
        [rule("no-cast", "project.yml")], [], [rule("no-cast", "global.yml")]
    )


def test_local_id_matching_global_id_is_an_override_not_an_error() -> None:
    check_id_conflicts(
        [], [rule("no-cast", "local.yml")], [rule("no-cast", "global.yml")]
    )


def test_project_id_matching_local_id_is_an_error() -> None:
    with pytest.raises(DuplicateRuleId, match="requires a different ID"):
        check_id_conflicts(
            [rule("no-cast", "project.yml")], [rule("no-cast", "local.yml")], []
        )


def test_scope_rules_dirs_map_to_repo_paths_and_canonical_global_root() -> None:
    repo_root = Path("/repo")
    global_root = Path("/home/user/.config/byolsp/rules")
    paths = RepoPaths()

    args = (repo_root, paths, global_root)
    assert scope_rules_dir("project", *args) == repo_root / ".byolsp/rules/project"
    assert scope_rules_dir("local", *args) == repo_root / ".byolsp/rules/personal/local"
    assert scope_rules_dir("global", *args) == global_root
