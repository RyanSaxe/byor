"""`byor install`: global, one-time registration of byor's AI integrations."""

from pathlib import Path

import pytest
from support import global_agents

from byor.cli import main


def test_install_sets_up_the_global_ast_grep_config_and_skill(home: Path) -> None:
    assert main(["install", "--non-interactive", "--agents", "claude-code"]) == 0

    sgconfig = home / "sgconfig.yml"
    assert sgconfig.is_file()
    assert "xdg/byor/rules" in sgconfig.read_text()
    assert (home / "xdg" / "byor" / "rules").is_dir()
    # The skill installs by default, to its global location.
    assert (home / ".agents" / "skills" / "byor" / "SKILL.md").is_file()


def test_install_records_agents_globally_and_adds_the_skill(home: Path) -> None:
    assert main(["install", "--non-interactive", "--agents", "claude-code,codex"]) == 0

    assert global_agents() == ["claude-code", "codex", "skill"]


def test_install_is_idempotent_and_merges_without_duplicates(home: Path) -> None:
    main(["install", "--non-interactive", "--agents", "claude-code,codex"])
    main(["install", "--non-interactive", "--agents", "claude-code,cursor"])

    assert global_agents() == ["claude-code", "codex", "skill", "cursor"]


def test_unknown_agent_fails_cleanly(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["install", "--non-interactive", "--agents", "frobnicate"]) == 1
    assert "Unknown agents: frobnicate" in capsys.readouterr().err
