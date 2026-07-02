"""Exercise global install command behavior.

These tests document the public behavior expected from the surrounding package area. Keeping that
intent at module scope helps the dogfooding contract distinguish purposeful coverage from incidental
implementation checks.
"""

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


@pytest.mark.usefixtures("home")
def test_install_records_agents_globally_and_adds_the_skill() -> None:
    assert main(["install", "--non-interactive", "--agents", "claude-code,codex"]) == 0

    assert global_agents() == ["claude-code", "codex", "skill"]


@pytest.mark.usefixtures("home")
def test_install_is_idempotent_and_merges_without_duplicates() -> None:
    main(["install", "--non-interactive", "--agents", "claude-code,codex"])
    main(["install", "--non-interactive", "--agents", "claude-code,copilot"])

    assert global_agents() == ["claude-code", "codex", "skill", "copilot"]


@pytest.mark.usefixtures("home")
def test_unknown_agent_fails_cleanly(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["install", "--non-interactive", "--agents", "frobnicate"]) == 1
    assert "Unknown agents: frobnicate" in capsys.readouterr().err
