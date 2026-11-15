"""The OpenCode adapter: post-edit plugin (global registration).

OpenCode has no hook-config JSON to merge into; byor writes a plugin file it owns outright,
identified by an embedded marker. The marker is the ownership contract: uninstall removes only
marker-bearing files, and doctor flags a missing or drifted plugin that a re-install repairs.
"""

from pathlib import Path

import pytest
from support import global_agents, repo_with_agents

from byor.agents.opencode import OPENCODE_MARKER, OPENCODE_PLUGIN, OPENCODE_PLUGIN_RELPATH
from byor.cli import main


def test_plugin_renders_the_packaged_source_without_placeholders() -> None:
    # The plugin ships as a packaged .ts file; rendering substitutes every
    # {{...}} placeholder so the installed artifact is plain TypeScript.
    assert OPENCODE_PLUGIN.startswith(OPENCODE_MARKER + "\n")
    assert "{{" not in OPENCODE_PLUGIN
    assert OPENCODE_PLUGIN.endswith("\n")


def test_install_writes_the_plugin(home: Path) -> None:
    repo_with_agents(home, "opencode")

    # The plugin cannot run against a real OpenCode here; assert its shape.
    plugin = (home / OPENCODE_PLUGIN_RELPATH).read_text()
    assert plugin.startswith(OPENCODE_MARKER)
    # opencode 2 refuses a plugin without a default export carrying an id and
    # a setup function; opencode 1 reads `server` from the same object.
    assert "export default {" in plugin
    assert 'id: "byor"' in plugin
    assert "setup(ctx)" in plugin
    assert 'ctx.tool.hook("execute.after"' in plugin
    assert "server: async ({ directory })" in plugin
    assert '"tool.execute.after"' in plugin
    # edit/write carry `path` (opencode 2) or `filePath` (opencode 1); the patch
    # tool is `patch` (opencode 2) or `apply_patch` (opencode 1).
    assert '["edit", "write"]' in plugin
    assert '["apply_patch", "patch"]' in plugin
    assert '"path"' in plugin
    assert '"filePath"' in plugin
    assert "patchText" in plugin  # patch-tool paths come from the patch text
    assert '"agent-check", "--scope", "diff", "--files"' in plugin
    # Only exit 2 appends anything, so a byor error never breaks the loop.
    assert "error?.code === DIAGNOSTICS_EXIT_CODE" in plugin

    assert "opencode" in global_agents()


def test_uninstall_removes_only_marker_bearing_files(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
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

    corrupt = OPENCODE_MARKER.encode() + b"\ncaf\xe9\n"
    for breakage in (
        plugin.unlink,
        lambda: plugin.write_text(OPENCODE_MARKER + "\n"),
        lambda: plugin.write_bytes(corrupt),
    ):
        breakage()
        broken = plugin.read_bytes() if plugin.is_file() else None
        capsys.readouterr()

        assert main(["doctor", "--repo", str(repo), "--quick"]) == 1
        out = capsys.readouterr().out
        assert "FAIL  agent_files" in out
        assert OPENCODE_PLUGIN_RELPATH in out
        assert "run `byor install`" in out
        # Doctor is read-only: the broken plugin stays exactly as it was.
        assert (plugin.read_bytes() if plugin.is_file() else None) == broken

        assert main(["hook", "install", "--agent", "opencode"]) == 0
        assert plugin.read_text() == OPENCODE_PLUGIN
        assert main(["doctor", "--repo", str(repo), "--quick"]) == 0
