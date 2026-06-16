"""The byor rule-capture skill: rendering, global installation, self-heal."""

import re
from pathlib import Path

import pytest
from support import global_agents, install_agents, make_repo

from byor.agents.install import MANAGED_MARKER
from byor.cli import main
from byor.io.yamlio import parse_yaml_mapping
from byor.rules.rules import ALLOW_EXCEPTIONS_SENTENCE
from byor.rules.skill import global_skill_paths

SKILL_NAME_PATTERN = r"[a-z0-9]+(-[a-z0-9]+)*"

MAX_NAME_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 1024


def test_install_renders_the_skill_into_both_global_locations(home: Path) -> None:
    install_agents(home)

    contents = [path.read_text() for path in global_skill_paths(home)]
    assert contents[0] == contents[1]
    assert "skill" in global_agents()

    content = contents[0]
    assert content.startswith("---\n")  # frontmatter at byte 0
    after_frontmatter = content.split("---\n", 2)[2]
    assert after_frontmatter.startswith(MANAGED_MARKER)


def test_frontmatter_meets_the_cross_agent_standard(home: Path) -> None:
    install_agents(home)

    frontmatter_text = global_skill_paths(home)[0].read_text().split("---\n", 2)[1]
    frontmatter = parse_yaml_mapping(frontmatter_text, source=Path("SKILL.md"))

    name = frontmatter["name"]
    description = frontmatter["description"]
    assert re.fullmatch(SKILL_NAME_PATTERN, name)
    assert 1 <= len(name) <= MAX_NAME_LENGTH
    assert len(description) <= MAX_DESCRIPTION_LENGTH
    assert "never" in description  # states when to trigger


def test_skill_teaches_the_full_capture_loop(home: Path) -> None:
    install_agents(home)
    content = global_skill_paths(home)[0].read_text()

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


def test_skill_teaches_authoring_a_check_script(home: Path) -> None:
    install_agents(home)
    content = global_skill_paths(home)[0].read_text()

    # The script path: pick a tool, or write a check script for bespoke logic.
    assert "Author a check script" in content
    assert "without a shell" in content
    assert "trailing path arguments" in content


def test_uninstall_removes_only_marked_renders(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    install_agents(home)
    user_render = global_skill_paths(home)[1]
    user_render.write_text("my own skill\n")

    assert main(["hook", "uninstall", "--agent", "skill"]) == 0

    assert not global_skill_paths(home)[0].exists()
    assert user_render.read_text() == "my own skill\n"
    assert "without the BYOR marker" in capsys.readouterr().out
    assert "skill" not in global_agents()


def test_self_heal_refreshes_a_drifted_skill_render(home: Path) -> None:
    """byor owns the skill, so running any command rewrites a managed render
    that drifted from the packaged skill — no explicit reinstall needed."""
    repo = make_repo(home)
    install_agents(home)
    drifted = global_skill_paths(home)[0]
    drifted.write_text(f"{MANAGED_MARKER}\nstale render\n")

    assert main(["list", "--repo", str(repo)]) == 0  # any command self-heals

    assert drifted.read_text() == global_skill_paths(home)[1].read_text()
    assert "BYOR Rule Capture" in drifted.read_text()


def test_self_heal_leaves_a_user_owned_render_untouched(home: Path) -> None:
    """Dropping the marker hands the file to the user; self-heal never clobbers
    it, the standard ownership escape hatch."""
    repo = make_repo(home)
    install_agents(home)
    owned = global_skill_paths(home)[0]
    owned.write_text("# our house skill\n")  # no marker: user-owned

    assert main(["list", "--repo", str(repo)]) == 0

    assert owned.read_text() == "# our house skill\n"


def test_install_writes_the_hook_and_global_skill(home: Path) -> None:
    """`byor install --agents claude-code` registers a global hook alongside the
    global skill render under ~/.claude/skills/."""
    install_agents(home, "claude-code")

    assert (home / ".claude" / "settings.json").is_file()
    assert global_skill_paths(home)[1].is_file()
