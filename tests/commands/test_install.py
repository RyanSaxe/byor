"""`byor install`: global, one-time registration of byor's AI integrations.

install touches only machine-global state — the home sgconfig, the skill render, and the recorded
agent list — never a repository. The tests pin idempotency (re-running merges agents without
duplicates) and a clean failure for an unknown agent name.
"""

import io
import sys
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


# A piped `byor install` used to print 'Enter numbers separated by commas [1]:'
# before falling back to the default; a prompt no one can answer is noise.
@pytest.mark.usefixtures("home")
def test_install_suppresses_the_prompt_when_stdin_is_not_a_tty(
    # monkeypatch isolates process state (env, cwd, stdio): an external boundary
    # ast-grep-ignore: python.question-mocks
    monkeypatch: pytest.MonkeyPatch,
    *,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))

    assert main(["install"]) == 0

    assert "Enter numbers separated by commas" not in capsys.readouterr().out
    assert global_agents() == ["claude-code", "skill"]  # the prompt's default still applies


@pytest.mark.usefixtures("home")
def test_install_still_reads_a_piped_answer(
    # monkeypatch isolates process state (env, cwd, stdio): an external boundary
    # ast-grep-ignore: python.question-mocks
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO("2\n"))

    assert main(["install"]) == 0

    assert global_agents() == ["codex", "skill"]
