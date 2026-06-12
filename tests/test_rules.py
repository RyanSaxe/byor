from pathlib import Path

import pytest

from byolsp.errors import ConfigError, RuleValidationError
from byolsp.rules import (
    ByolspMetadata,
    discover_rule_files,
    load_rule,
    load_rules,
    rule_id_warnings,
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

    with pytest.raises(ConfigError, match=r"invalid\.yml"):
        load_rule(path)


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
