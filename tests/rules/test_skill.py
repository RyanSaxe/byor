"""The byor skill: rendering the hub + references, global installation, self-heal."""

import re
from pathlib import Path

import pytest
from support import global_agents, install_agents, make_repo

from byor.agents.install import MANAGED_MARKER
from byor.cli import main
from byor.io.yamlio import parse_yaml_mapping
from byor.rules.rules import ALLOW_EXCEPTIONS_SENTENCE
from byor.rules.skill import global_skill_dirs

SKILL_NAME_PATTERN = r"[a-z0-9]+(-[a-z0-9]+)*"

MAX_NAME_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 1024

REFERENCES = (
    "references/patterns.md",
    "references/checks.md",
    "references/packages.md",
    "references/profiles.md",
    "references/setup.md",
)


def agents_dir(home: Path) -> Path:
    """The cross-agent skill dir (read by Codex, Copilot, opencode, pi)."""
    return global_skill_dirs(home)[0]


def claude_dir(home: Path) -> Path:
    """The Claude Code skill dir, which reads only its own directory."""
    return global_skill_dirs(home)[1]


def test_install_renders_the_whole_tree_into_both_global_locations(home: Path) -> None:
    install_agents(home)

    for relpath in ("SKILL.md", *REFERENCES):
        agents_copy = (agents_dir(home) / relpath).read_text()
        claude_copy = (claude_dir(home) / relpath).read_text()
        assert agents_copy == claude_copy
    assert "skill" in global_agents()

    hub = (agents_dir(home) / "SKILL.md").read_text()
    assert hub.startswith("---\n")  # frontmatter at byte 0
    after_frontmatter = hub.split("---\n", 2)[2]
    assert after_frontmatter.startswith(MANAGED_MARKER)

    # Reference files carry no frontmatter, so the marker leads the file.
    for relpath in REFERENCES:
        assert (agents_dir(home) / relpath).read_text().startswith(MANAGED_MARKER)


def test_frontmatter_meets_the_cross_agent_standard(home: Path) -> None:
    install_agents(home)

    hub = (agents_dir(home) / "SKILL.md").read_text()
    frontmatter = parse_yaml_mapping(hub.split("---\n", 2)[1], source=Path("SKILL.md"))

    name = frontmatter["name"]
    description = frontmatter["description"]
    assert re.fullmatch(SKILL_NAME_PATTERN, name)
    assert 1 <= len(name) <= MAX_NAME_LENGTH
    assert len(description) <= MAX_DESCRIPTION_LENGTH
    assert "never" in description  # states the capture trigger
    assert "set up" in description  # and the setup trigger


def test_hub_teaches_the_capture_loop(home: Path) -> None:
    install_agents(home)
    hub = (agents_dir(home) / "SKILL.md").read_text()

    assert "byor add --scope" in hub
    assert "--from" in hub
    assert "ast-grep scan" in hub
    # The single confirmation question folds in "are exceptions allowed?".
    assert "whether exceptions" in hub
    assert ALLOW_EXCEPTIONS_SENTENCE in hub
    # The hub points to each reference rather than restating them.
    for relpath in REFERENCES:
        assert relpath in hub


def test_patterns_reference_has_primer_and_worked_example(home: Path) -> None:
    install_agents(home)
    patterns = (agents_dir(home) / "references/patterns.md").read_text()

    assert "never use print for logging" in patterns
    assert "$$$" in patterns
    assert "ast-grep run -p" in patterns


def test_checks_reference_teaches_authoring_a_check_script(home: Path) -> None:
    install_agents(home)
    checks = (agents_dir(home) / "references/checks.md").read_text()

    assert "Author a check script" in checks
    assert "without a shell" in checks
    assert "trailing path arguments" in checks


def test_packages_reference_teaches_authoring_a_package(home: Path) -> None:
    install_agents(home)
    packages = (agents_dir(home) / "references/packages.md").read_text()

    assert "byor package add" in packages
    assert "~/.config/byor/packages/" in packages
    assert "byor promote --check" in packages


def test_profiles_reference_teaches_exclusions_and_profiles(home: Path) -> None:
    install_agents(home)
    profiles = (agents_dir(home) / "references/profiles.md").read_text()

    assert "byor exclude" in profiles
    assert "byor profile add" in profiles
    assert "excluded_tags" in profiles


def test_setup_reference_teaches_onboarding(home: Path) -> None:
    install_agents(home)
    setup = (agents_dir(home) / "references/setup.md").read_text()

    assert "byor doctor" in setup
    assert "uv tool install byor" in setup  # the one bootstrap step a skill can't do
    assert "byor init" in setup
    assert "byor/initial-cleanup" in setup  # the optional cleanup pass


def test_uninstall_removes_marked_files_and_prunes_dirs(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    install_agents(home)
    user_file = claude_dir(home) / "references" / "patterns.md"
    user_file.write_text("my own notes\n")  # no marker: user-owned

    assert main(["hook", "uninstall", "--agent", "skill"]) == 0

    # Marked files (and the dirs they left empty) are gone; the user file stays.
    assert not (agents_dir(home) / "SKILL.md").exists()
    assert not agents_dir(home).exists()  # fully pruned
    assert user_file.read_text() == "my own notes\n"
    assert "without the BYOR marker" in capsys.readouterr().out
    assert "skill" not in global_agents()


def test_self_heal_refreshes_a_drifted_reference(home: Path) -> None:
    """byor owns the skill, so running any command rewrites a managed file that
    drifted from the package — references included, not just the hub."""
    repo = make_repo(home)
    install_agents(home)
    drifted = agents_dir(home) / "references" / "setup.md"
    drifted.write_text(f"{MANAGED_MARKER}\nstale render\n")

    assert main(["list", "--repo", str(repo)]) == 0  # any command self-heals

    assert drifted.read_text() == (claude_dir(home) / "references/setup.md").read_text()
    assert "Set Up byor" in drifted.read_text()


def test_self_heal_leaves_a_user_owned_file_untouched(home: Path) -> None:
    """Dropping the marker hands the file to the user; self-heal never clobbers
    it, the standard ownership escape hatch."""
    repo = make_repo(home)
    install_agents(home)
    owned = agents_dir(home) / "SKILL.md"
    owned.write_text("# our house skill\n")  # no marker: user-owned

    assert main(["list", "--repo", str(repo)]) == 0

    assert owned.read_text() == "# our house skill\n"


def test_install_writes_the_hook_and_global_skill(home: Path) -> None:
    """`byor install --agents claude-code` registers a global hook alongside the
    global skill render under ~/.claude/skills/."""
    install_agents(home, "claude-code")

    assert (home / ".claude" / "settings.json").is_file()
    assert (claude_dir(home) / "SKILL.md").is_file()
