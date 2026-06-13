"""The byor rule-capture skill: rendering, installation, doctor."""

import re
from pathlib import Path

import pytest
from conftest import make_repo

from byor.agents import MANAGED_MARKER
from byor.cli import main
from byor.config import load_repo_config
from byor.rules import ALLOW_EXCEPTIONS_SENTENCE
from byor.skill import SKILL_RELPATHS
from byor.yamlio import parse_yaml_mapping

SKILL_NAME_PATTERN = r"[a-z0-9]+(-[a-z0-9]+)*"

MAX_NAME_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 1024


def rendered_skill(repo: Path) -> str:
    return (repo / SKILL_RELPATHS[0]).read_text()


def test_init_renders_the_skill_into_both_locations_by_default(home: Path) -> None:
    repo = make_repo(home)

    contents = [(repo / relpath).read_text() for relpath in SKILL_RELPATHS]
    assert contents[0] == contents[1]
    assert "skill" in load_repo_config(repo).agents

    content = contents[0]
    assert content.startswith("---\n")  # frontmatter at byte 0
    after_frontmatter = content.split("---\n", 2)[2]
    assert after_frontmatter.startswith(MANAGED_MARKER)


def test_frontmatter_meets_the_cross_agent_standard(home: Path) -> None:
    repo = make_repo(home)

    frontmatter_text = rendered_skill(repo).split("---\n", 2)[1]
    frontmatter = parse_yaml_mapping(frontmatter_text, source=Path("SKILL.md"))

    name = frontmatter["name"]
    description = frontmatter["description"]
    assert re.fullmatch(SKILL_NAME_PATTERN, name)
    assert 1 <= len(name) <= MAX_NAME_LENGTH
    assert len(description) <= MAX_DESCRIPTION_LENGTH
    assert "never" in description  # states when to trigger


def test_skill_teaches_the_full_capture_loop(home: Path) -> None:
    content = rendered_skill(make_repo(home))

    # Create, verify, decline, worked example, and the pattern primer.
    assert "byor add --scope" in content
    assert "--from" in content
    assert "ast-grep scan" in content
    assert "never use print for logging" in content
    assert "$$$" in content
    assert "ast-grep run -p" in content
    # The single confirmation question folds in "are exceptions allowed?".
    assert "whether exceptions" in content
    assert ALLOW_EXCEPTIONS_SENTENCE in content


def test_hook_uninstall_removes_only_marked_renders(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_repo(home)
    user_render = repo / SKILL_RELPATHS[1]
    user_render.write_text("my own skill\n")

    assert main(["hook", "uninstall", "--repo", str(repo), "--agent", "skill"]) == 0

    assert not (repo / SKILL_RELPATHS[0]).exists()
    assert user_render.read_text() == "my own skill\n"
    assert "without the BYOR marker" in capsys.readouterr().out
    assert "skill" not in load_repo_config(repo).agents


def test_doctor_flags_missing_or_drifted_renders_and_install_repairs(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_repo(home)
    (repo / SKILL_RELPATHS[0]).unlink()
    drifted = repo / SKILL_RELPATHS[1]
    drifted.write_text(f"{MANAGED_MARKER}\nstale render\n")
    capsys.readouterr()

    assert main(["doctor", "--repo", str(repo), "--quick"]) == 1
    out = capsys.readouterr().out
    assert SKILL_RELPATHS[0] in out
    assert SKILL_RELPATHS[1] in out

    assert main(["hook", "install", "--repo", str(repo), "--agent", "skill"]) == 0
    assert main(["doctor", "--repo", str(repo), "--quick"]) == 0


def test_claude_code_install_writes_both_the_hook_and_instructions(home: Path) -> None:
    """claude-code installs a real hook plus its instruction file,
    alongside the skill render the layout already plants under .claude/skills/.
    """
    repo = make_repo(home)

    assert main(["hook", "install", "--repo", str(repo), "--agent", "claude-code"]) == 0

    assert (repo / ".byor" / "agents" / "claude-code.md").is_file()
    assert (repo / ".claude" / "settings.json").is_file()
    assert (repo / SKILL_RELPATHS[1]).is_file()
