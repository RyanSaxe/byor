"""The OpenCode adapter: post-edit plugin (global registration)."""

from pathlib import Path

import pytest
from support import global_agents, repo_with_agents

from byor.agents.opencode import OPENCODE_MARKER, OPENCODE_PLUGIN_RELPATH
from byor.cli import main


def test_install_writes_the_plugin(home: Path) -> None:
    repo_with_agents(home, "opencode")

    # The plugin cannot run against a real OpenCode here; assert its shape.
    plugin = (home / OPENCODE_PLUGIN_RELPATH).read_text()
    assert plugin.startswith(OPENCODE_MARKER)
    assert '"tool.execute.after"' in plugin
    assert '["edit", "write", "apply_patch"]' in plugin
    assert "patchText" in plugin  # apply_patch paths come from the patch text
    assert "byor agent-check --scope diff --files" in plugin
    assert ".nothrow()" in plugin  # exit codes other than 2 never break the loop
    assert "output.output" in plugin

    assert "opencode" in global_agents()


def test_uninstall_removes_only_marker_bearing_files(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo_with_agents(home, "opencode")
    plugin = home / OPENCODE_PLUGIN_RELPATH
    plugin.write_text("export const MyPlugin = {}\n")  # marker gone: user-owned

    assert main(["hook", "uninstall", "--agent", "opencode"]) == 0

    assert plugin.read_text() == "export const MyPlugin = {}\n"
    assert "without the BYOR marker" in capsys.readouterr().out
    assert "opencode" not in global_agents()


def test_doctor_flags_a_missing_or_drifted_plugin_and_install_repairs_it(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = repo_with_agents(home, "opencode")
    plugin = home / OPENCODE_PLUGIN_RELPATH

    for breakage in (plugin.unlink, lambda: plugin.write_text(OPENCODE_MARKER + "\n")):
        breakage()
        capsys.readouterr()
        assert main(["doctor", "--repo", str(repo), "--quick"]) == 1
        assert OPENCODE_PLUGIN_RELPATH in capsys.readouterr().out

        assert main(["hook", "install", "--agent", "opencode"]) == 0
        assert main(["doctor", "--repo", str(repo), "--quick"]) == 0
