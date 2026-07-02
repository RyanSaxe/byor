"""Exercise doctor health checks.

Doctor is byor's read-only diagnosis, so these tests pin the healthy-repo report, the documented
JSON shape, and a finding for each way an install decays: missing sgconfig or ast-grep, invalid
rules, duplicate ids, stale gate files or package mirrors, dead registry paths, drifted skill
renders. It must stay graceful outside a byor repo, and quick mode skips the recursive rule
validation.
"""

import json
import shutil
import sys
from pathlib import Path

import pytest
from support import (
    git,
    install_agents,
    install_package,
    make_repo,
    repo_with_agents,
    write_package_rule,
    write_rule,
)

from byor.agents.opencode import OPENCODE_PLUGIN_RELPATH
from byor.cli import main
from byor.commands.doctor import collect_checks
from byor.config import (
    CheckDef,
    LocalConfig,
    load_repo_config,
    save_local_config,
    save_repo_config,
)
from byor.scan.astgrep import NOT_FOUND_MESSAGE


def test_doctor_reports_ok_for_a_healthy_repo(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = make_repo(home)
    capsys.readouterr()

    assert main(["doctor", "--repo", str(repo)]) == 0

    out = capsys.readouterr().out
    assert "ok    ast_grep_found" in out
    assert "FAIL" not in out


def test_doctor_json_matches_the_spec_shape(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = make_repo(home)
    capsys.readouterr()

    assert main(["doctor", "--repo", str(repo), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    first = payload["checks"][0]
    assert first["id"] == "ast_grep_found"
    assert first["ok"] is True
    assert first["message"].startswith("ast-grep ")
    assert {check["id"] for check in payload["checks"]} == {
        "ast_grep_found",
        "home_sgconfig",
        "agent_files",
        "registered_repos",
        "global_rules",
        "repo_config",
        "sgconfig",
        "rule_dirs",
        "rules_visible",
        "rules_valid",
        "rule_ids_unique",
        "sync_fresh",
    }


def test_doctor_surfaces_configured_checks_with_origin(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = make_repo(home)
    config = load_repo_config(repo)
    config.checks.append(CheckDef("ruff", ["py"], "uv run ruff check"))
    save_repo_config(repo, config)
    capsys.readouterr()

    assert main(["doctor", "--repo", str(repo)]) == 0

    out = capsys.readouterr().out
    assert "extra_checks" in out
    assert "checks: ruff (repo)" in out


def test_doctor_extra_checks_reports_when_all_are_excluded(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = make_repo(home)
    config = load_repo_config(repo)
    config.checks.append(CheckDef("ruff", ["py"], "uv run ruff check"))
    save_repo_config(repo, config)
    save_local_config(repo, LocalConfig(excluded_checks=["ruff"]))
    capsys.readouterr()

    assert main(["doctor", "--repo", str(repo)]) == 0

    assert "all configured checks are excluded" in capsys.readouterr().out


def test_doctor_reports_missing_ast_grep_with_the_install_message(
    home: Path, monkeypatch: pytest.MonkeyPatch, *, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_repo(home)
    empty_bin = home / "empty-bin"
    empty_bin.mkdir()
    monkeypatch.setenv("PATH", str(empty_bin))
    monkeypatch.delenv("BYOR_AST_GREP", raising=False)
    # Also hide the bundled ast-grep beside the interpreter (the auto fallback).
    monkeypatch.setattr(sys, "executable", str(empty_bin / "python"))
    capsys.readouterr()

    assert main(["doctor", "--repo", str(repo)]) == 1

    assert NOT_FOUND_MESSAGE in capsys.readouterr().out


def test_doctor_global_section_is_healthy_after_install(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    install_agents("claude-code")
    elsewhere = home / "elsewhere"
    elsewhere.mkdir()
    capsys.readouterr()

    assert main(["doctor", "--repo", str(elsewhere)]) == 0

    out = capsys.readouterr().out
    assert "~/sgconfig.yml applies your global rules" in out
    assert "agent integrations installed for: claude-code, skill" in out


def test_doctor_reports_a_non_byor_repo_gracefully(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = home / "untouched"
    repo.mkdir()
    capsys.readouterr()

    # Global health is fine; the repo section degrades to an informational line.
    assert main(["doctor", "--repo", str(repo)]) == 0

    out = capsys.readouterr().out
    assert "ok    repo" in out
    assert "not a byor repo" in out
    assert "byor init" in out


def test_doctor_flags_missing_sgconfig(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = make_repo(home)
    (repo / "sgconfig.yml").unlink()
    capsys.readouterr()

    assert main(["doctor", "--repo", str(repo)]) == 1

    assert "sgconfig.yml is missing; run `byor init`" in capsys.readouterr().out


def test_doctor_renders_a_failing_check_for_invalid_rules(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = make_repo(home)
    broken = repo / ".byor" / "rules" / "project" / "broken.yml"
    broken.write_text("id: broken\n")
    capsys.readouterr()

    assert main(["doctor", "--repo", str(repo)]) == 1

    captured = capsys.readouterr()
    assert "FAIL  rules_valid" in captured.out
    assert "broken.yml: missing required ast-grep fields" in captured.out
    assert "Traceback" not in captured.err


def test_quick_checks_skip_recursive_rule_validation(home: Path) -> None:
    repo = make_repo(home)
    broken = repo / ".byor" / "rules" / "project" / "broken.yml"
    broken.write_text("id: broken\n")
    config_dir = home / "xdg" / "byor"

    full = collect_checks(repo, config_dir, quick=False)
    quick = collect_checks(repo, config_dir, quick=True)

    failed = {check.id: check for check in full if not check.ok}
    assert set(failed) == {"rules_valid"}
    assert "missing required ast-grep fields" in failed["rules_valid"].message
    assert all(check.ok for check in quick)


def test_doctor_flags_a_missing_rule_visibility_file(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = make_repo(home)
    (repo / ".byor" / "rules" / "personal" / "local" / ".ignore").unlink()
    capsys.readouterr()

    assert main(["doctor", "--repo", str(repo), "--quick"]) == 1

    out = capsys.readouterr().out
    assert "FAIL  rules_visible" in out
    assert ".byor/rules/personal/local" in out


def test_doctor_flags_duplicate_project_and_local_ids(home: Path) -> None:
    repo = make_repo(home)
    write_rule(repo / ".byor" / "rules" / "project" / "no-cast.yml", "no-cast")
    write_rule(repo / ".byor" / "rules" / "personal" / "local" / "no-cast.yml", "no-cast")

    checks = collect_checks(repo, home / "xdg" / "byor", quick=False)

    failed = {check.id: check for check in checks if not check.ok}
    assert set(failed) == {"rule_ids_unique"}
    assert "no-cast" in failed["rule_ids_unique"].message


def test_doctor_flags_registered_repos_whose_path_is_gone(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = make_repo(home)
    gone = make_repo(home, name="gone")
    shutil.rmtree(gone)
    capsys.readouterr()

    assert main(["doctor", "--repo", str(repo)]) == 1

    assert f"{gone} no longer exists" in capsys.readouterr().out


def test_doctor_flags_a_missing_opencode_plugin(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = repo_with_agents(home, "opencode")
    plugin = home / OPENCODE_PLUGIN_RELPATH
    plugin.unlink()
    capsys.readouterr()

    assert main(["doctor", "--repo", str(repo), "--quick"]) == 1

    out = capsys.readouterr().out
    assert "~/.config/opencode/plugin/byor.ts is missing" in out
    assert "run `byor install`" in out
    # Doctor is read-only: reporting the problem must not rewrite the plugin.
    assert not plugin.exists()


def test_doctor_reports_a_malformed_agent_config_as_a_failing_check(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = repo_with_agents(home, "claude-code")
    settings = home / ".claude" / "settings.json"
    settings.write_text("{not json")
    capsys.readouterr()

    assert main(["doctor", "--repo", str(repo), "--quick"]) == 1

    captured = capsys.readouterr()
    assert "FAIL  agent_files" in captured.out
    assert ".claude/settings.json is not valid JSON" in captured.out
    assert "fix the JSON by hand" in captured.out
    # The parse error is a check row, not an escaped top-level `byor:` error.
    assert "byor:" not in captured.err
    assert "Traceback" not in captured.err
    # Doctor is read-only: the malformed file is left for the user to fix.
    assert settings.read_text() == "{not json"


def test_doctor_flags_a_drifted_skill_render(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = repo_with_agents(home, "skill")
    skill = home / ".claude" / "skills" / "byor" / "SKILL.md"
    edited = skill.read_text() + "\nlocal note\n"  # marker kept: managed but drifted
    skill.write_text(edited)
    capsys.readouterr()

    assert main(["doctor", "--repo", str(repo), "--quick"]) == 1

    out = capsys.readouterr().out
    assert "FAIL  agent_files" in out
    assert "~/.claude/skills/byor/SKILL.md is out of date" in out
    assert "run `byor install`" in out
    # Doctor is read-only: the drifted render stays as the user left it.
    assert skill.read_text() == edited


def test_doctor_flags_stale_gate_files(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = home / "gated"
    repo.mkdir()
    git(repo, "init", "--quiet")
    assert main(["init", "--repo", str(repo), "--non-interactive", "--gate"]) == 0
    capsys.readouterr()
    assert main(["doctor", "--repo", str(repo)]) == 0
    assert "ok    gate_files" in capsys.readouterr().out

    config = load_repo_config(repo)
    config.checks.append(CheckDef("ruff", ["py"], "uv run ruff check"))
    save_repo_config(repo, config)
    capsys.readouterr()

    assert main(["doctor", "--repo", str(repo)]) == 1

    out = capsys.readouterr().out
    assert "FAIL  gate_files" in out
    assert "run `byor init --gate`" in out
    # Doctor is read-only: the gate artifacts still lack the new check.
    assert "ruff" not in (repo / ".github" / "workflows" / "byor-gate.yml").read_text()
    assert "ruff" not in (repo / ".pre-commit-config.yaml").read_text()


def test_doctor_flags_a_missing_packages_visibility_file(home: Path) -> None:
    write_package_rule(home, "pkg", relpath="no-cast.yml", rule_id="pkg-no-cast")
    repo = make_repo(home)
    install_package(repo, "pkg")
    assert main(["sync", "--repo", str(repo)]) == 0
    (repo / ".byor" / "rules" / "personal" / "packages" / ".ignore").unlink()

    checks = collect_checks(repo, home / "xdg" / "byor", quick=False)

    failed = {check.id for check in checks if not check.ok}
    assert "rules_visible" in failed


def test_doctor_flags_a_stale_packages_mirror(home: Path) -> None:
    write_package_rule(home, "pkg", relpath="no-cast.yml", rule_id="pkg-no-cast")
    repo = make_repo(home)
    install_package(repo, "pkg")
    assert main(["sync", "--repo", str(repo)]) == 0
    mirror = repo / ".byor" / "rules" / "personal" / "packages" / "pkg" / "no-cast.yml"
    mirror.unlink()  # mirror now diverges from the package source

    checks = collect_checks(repo, home / "xdg" / "byor", quick=False)

    failed = {check.id for check in checks if not check.ok}
    assert "sync_fresh" in failed
