"""`byor install`: global, one-time registration of byor's AI integrations.

install touches only machine-global state — the home sgconfig, the skill render, and the recorded
agent list — never a repository. The tests pin idempotency (re-running merges agents without
duplicates) and a clean failure for an unknown agent name.
"""

import io
import os
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


POSIX_NON_ROOT = pytest.mark.skipif(
    sys.platform == "win32" or os.geteuid() == 0, reason="needs POSIX permissions as a non-root user"
)


@POSIX_NON_ROOT
def test_install_refuses_a_read_only_settings_file_and_installs_the_rest(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = home / ".claude" / "settings.json"
    settings.parent.mkdir()
    settings.write_text('{"theme": "dark"}\n')
    settings.chmod(0o444)
    capsys.readouterr()

    assert main(["install", "--agents", "claude-code,codex", "--non-interactive"]) == 1

    assert settings.read_text() == '{"theme": "dark"}\n'
    assert "cannot write" in capsys.readouterr().err
    assert "codex" in global_agents()


@POSIX_NON_ROOT
def test_install_still_installs_agents_when_a_read_only_sgconfig_cannot_list_the_rules(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sgconfig = home / "sgconfig.yml"
    sgconfig.write_text("ruleDirs:\n  - my-rules\n")
    sgconfig.chmod(0o444)
    capsys.readouterr()

    assert main(["install", "--agents", "claude-code", "--non-interactive"]) == 1

    assert "byor: skipped ~/sgconfig.yml" in capsys.readouterr().err
    assert "claude-code" in global_agents()


def test_install_and_doctor_name_a_dangling_global_rules_link(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rules = home / "xdg" / "byor" / "rules"
    rules.parent.mkdir(parents=True)
    rules.symlink_to(home / "moved-dotfiles" / "rules")
    capsys.readouterr()

    assert main(["install", "--agents", "skill", "--non-interactive"]) == 1
    assert f"{rules} is a symlink to" in capsys.readouterr().err

    assert main(["doctor", "--quick"]) == 1
    assert "FAIL  home_sgconfig     no global rule applies:" in capsys.readouterr().out
