"""The Pi adapter: a post-edit TypeScript extension."""

from pathlib import Path

import pytest
from support import make_repo

from byor.agents.pi import PI_EXTENSION_RELPATH, PI_MARKER
from byor.cli import main
from byor.config import load_repo_config


def test_install_writes_the_extension(home: Path) -> None:
    repo = make_repo(home, "repo", "--agents", "pi")

    # The extension cannot run against a real Pi here; assert its shape.
    extension = (repo / PI_EXTENSION_RELPATH).read_text()
    assert extension.startswith(PI_MARKER)
    assert 'pi.on("tool_result"' in extension
    assert '["edit", "write"]' in extension
    assert "byor" in extension and "agent-check" in extension

    assert "pi" in load_repo_config(repo).agents


def test_uninstall_removes_only_marker_bearing_files(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_repo(home, "repo", "--agents", "pi")
    extension = repo / PI_EXTENSION_RELPATH
    extension.write_text("export default () => {}\n")  # marker gone: user-owned

    assert main(["hook", "uninstall", "--repo", str(repo), "--agent", "pi"]) == 0

    assert extension.read_text() == "export default () => {}\n"
    assert "without the BYOR marker" in capsys.readouterr().out
    assert "pi" not in load_repo_config(repo).agents


def test_doctor_flags_a_missing_or_drifted_extension_and_install_repairs_it(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_repo(home, "repo", "--agents", "pi")
    extension = repo / PI_EXTENSION_RELPATH

    for breakage in (extension.unlink, lambda: extension.write_text(PI_MARKER + "\n")):
        breakage()
        capsys.readouterr()
        assert main(["doctor", "--repo", str(repo), "--quick"]) == 1
        assert PI_EXTENSION_RELPATH in capsys.readouterr().out

        cmd = ["hook", "install", "--repo", str(repo), "--agent", "pi"]
        assert main(cmd) == 0
        assert main(["doctor", "--repo", str(repo), "--quick"]) == 0


def test_pi_reads_the_skill_from_the_shared_agents_location(home: Path) -> None:
    """Pi discovers skills under .agents/skills/, the canonical render byor
    already writes, so installing pi needs no Pi-specific skill work."""
    repo = make_repo(home, "repo", "--agents", "pi")

    assert (repo / ".agents" / "skills" / "byor" / "SKILL.md").is_file()
