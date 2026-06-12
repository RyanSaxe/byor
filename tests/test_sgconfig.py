from pathlib import Path

import pytest

from byolsp.errors import ConfigError
from byolsp.sgconfig import ensure_rule_dirs

RULE_DIRS = [
    ".byolsp/rules/project",
    ".byolsp/rules/personal/local",
    ".byolsp/rules/personal/global",
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
        "# team scanner config\n"
        "ruleDirs:\n"
        "  - custom-rules\n"
        "  - .byolsp/rules/project\n"
        "utilDirs:\n"
        "  - utils\n"
    )

    message = ensure_rule_dirs(path, RULE_DIRS)

    assert message == "Updated sgconfig.yml"
    content = path.read_text()
    assert "# team scanner config" in content
    assert "utils" in content
    assert content.index("custom-rules") < content.index(".byolsp/rules/project")
    assert ".byolsp/rules/personal/local" in content
    assert ".byolsp/rules/personal/global" in content


def test_adds_rule_dirs_key_when_absent(tmp_path: Path) -> None:
    path = tmp_path / "sgconfig.yml"
    path.write_text("utilDirs:\n  - utils\n")

    assert ensure_rule_dirs(path, RULE_DIRS) == "Updated sgconfig.yml"
    assert ".byolsp/rules/project" in path.read_text()


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

    assert message is not None and "backup" in message
    backups = list(tmp_path.glob("sgconfig.yml.byolsp-backup-*"))
    assert len(backups) == 1
    assert backups[0].read_text() == "ruleDirs: not-a-list\n"
    assert all(rule_dir in path.read_text() for rule_dir in RULE_DIRS)
