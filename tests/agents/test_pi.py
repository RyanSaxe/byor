"""Exercise the Pi extension integration.

These tests document the public behavior expected from the surrounding package area. Keeping that
intent at module scope helps the dogfooding contract distinguish purposeful coverage from incidental
implementation checks.
"""

from pathlib import Path

import pytest
from support import global_agents, repo_with_agents

from byor.agents.pi import PI_EXTENSION, PI_EXTENSION_RELPATH, PI_MARKER
from byor.cli import main


def test_install_writes_the_extension(home: Path) -> None:
    repo_with_agents(home, "pi")

    # The extension cannot run against a real Pi here; assert its shape.
    extension = (home / PI_EXTENSION_RELPATH).read_text()
    assert extension.startswith(PI_MARKER)
    assert 'pi.on("tool_result"' in extension
    assert '["edit", "write"]' in extension
    assert "byor" in extension
    assert "agent-check" in extension

    assert "pi" in global_agents()


def test_uninstall_removes_only_marker_bearing_files(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo_with_agents(home, "pi")
    extension = home / PI_EXTENSION_RELPATH
    extension.write_text("export default () => {}\n")  # marker gone: user-owned

    assert main(["hook", "uninstall", "--agent", "pi"]) == 0

    assert extension.read_text() == "export default () => {}\n"
    assert "without the BYOR marker" in capsys.readouterr().out
    assert "pi" not in global_agents()


def test_doctor_flags_a_missing_or_drifted_extension_and_install_repairs_it(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = repo_with_agents(home, "pi")
    extension = home / PI_EXTENSION_RELPATH

    for breakage in (extension.unlink, lambda: extension.write_text(PI_MARKER + "\n")):
        breakage()
        broken = extension.read_text() if extension.is_file() else None
        capsys.readouterr()

        assert main(["doctor", "--repo", str(repo), "--quick"]) == 1
        out = capsys.readouterr().out
        assert "FAIL  agent_files" in out
        assert PI_EXTENSION_RELPATH in out
        assert "run `byor install`" in out
        # Doctor is read-only: the broken extension stays exactly as it was.
        assert (extension.read_text() if extension.is_file() else None) == broken

        assert main(["hook", "install", "--agent", "pi"]) == 0
        assert extension.read_text() == PI_EXTENSION
        assert main(["doctor", "--repo", str(repo), "--quick"]) == 0


def test_pi_reads_the_skill_from_the_shared_agents_location(home: Path) -> None:
    repo_with_agents(home, "pi")

    assert (home / ".agents" / "skills" / "byor" / "SKILL.md").is_file()
