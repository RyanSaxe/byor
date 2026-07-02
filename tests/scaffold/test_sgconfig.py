"""Exercise sgconfig scaffold behavior.

sgconfig.yml may predate byor, so scaffolding edits conservatively: create when missing, append only
the missing ruleDirs entries while preserving user content, and replace wholesale only after a
timestamped backup. The home sgconfig gets the same treatment, expressed relative to home.
"""

from pathlib import Path

import pytest

from byor.errors import ConfigError
from byor.io.paths import home_sgconfig_path
from byor.scaffold.sgconfig import ensure_home_sgconfig, ensure_rule_dirs

RULE_DIRS = [
    ".byor/rules/project",
    ".byor/rules/personal/local",
    ".byor/rules/personal/global",
]


def test_creates_sgconfig_when_missing(tmp_path: Path) -> None:
    path = tmp_path / "sgconfig.yml"

    message = ensure_rule_dirs(path, RULE_DIRS)

    assert message == "Created sgconfig.yml"
    content = path.read_text()
    assert all(rule_dir in content for rule_dir in RULE_DIRS)


def test_appends_missing_entries_preserving_user_content(tmp_path: Path) -> None:
    path = tmp_path / "sgconfig.yml"
    path.write_text(
        "# team scanner config\nruleDirs:\n  - custom-rules\n  - .byor/rules/project\nutilDirs:\n  - utils\n"
    )

    message = ensure_rule_dirs(path, RULE_DIRS)

    assert message == "Updated sgconfig.yml"
    content = path.read_text()
    assert "# team scanner config" in content
    assert "utils" in content
    assert content.index("custom-rules") < content.index(".byor/rules/project")
    assert ".byor/rules/personal/local" in content
    assert ".byor/rules/personal/global" in content


def test_adds_rule_dirs_key_when_absent(tmp_path: Path) -> None:
    path = tmp_path / "sgconfig.yml"
    path.write_text("utilDirs:\n  - utils\n")

    assert ensure_rule_dirs(path, RULE_DIRS) == "Updated sgconfig.yml"
    assert ".byor/rules/project" in path.read_text()


def test_no_change_when_already_complete(tmp_path: Path) -> None:
    path = tmp_path / "sgconfig.yml"
    ensure_rule_dirs(path, RULE_DIRS)
    before = path.read_text()

    assert ensure_rule_dirs(path, RULE_DIRS) is None
    assert path.read_text() == before


def test_rejects_non_list_rule_dirs_with_actionable_message(tmp_path: Path) -> None:
    path = tmp_path / "sgconfig.yml"
    path.write_text("ruleDirs: not-a-list\n")

    with pytest.raises(ConfigError, match="expected ruleDirs to be a list"):
        ensure_rule_dirs(path, RULE_DIRS)

    with pytest.raises(ConfigError, match="--replace-sgconfig"):
        ensure_rule_dirs(path, RULE_DIRS)


def test_replace_overwrites_after_timestamped_backup(tmp_path: Path) -> None:
    path = tmp_path / "sgconfig.yml"
    path.write_text("ruleDirs: not-a-list\n")

    message = ensure_rule_dirs(path, RULE_DIRS, replace=True)

    assert message is not None
    assert "backup" in message
    backups = list(tmp_path.glob("sgconfig.yml.byor-backup-*"))
    assert len(backups) == 1
    assert backups[0].read_text() == "ruleDirs: not-a-list\n"
    assert all(rule_dir in path.read_text() for rule_dir in RULE_DIRS)


def test_home_sgconfig_created_pointing_at_relative_rules_dir(tmp_path: Path) -> None:
    rules_dir = tmp_path / ".config" / "byor" / "rules"

    message = ensure_home_sgconfig(rules_dir, home=tmp_path)

    assert message == "Created sgconfig.yml"
    assert rules_dir.is_dir()
    content = home_sgconfig_path(tmp_path).read_text()
    assert ".config/byor/rules" in content


def test_home_sgconfig_appends_to_a_users_existing_config(tmp_path: Path) -> None:
    rules_dir = tmp_path / ".config" / "byor" / "rules"
    path = home_sgconfig_path(tmp_path)
    path.write_text("# my own global ast-grep setup\nruleDirs:\n  - my-rules\n")

    assert ensure_home_sgconfig(rules_dir, home=tmp_path) == "Updated sgconfig.yml"

    content = path.read_text()
    assert "# my own global ast-grep setup" in content
    assert "my-rules" in content
    assert ".config/byor/rules" in content
    assert ensure_home_sgconfig(rules_dir, home=tmp_path) is None
