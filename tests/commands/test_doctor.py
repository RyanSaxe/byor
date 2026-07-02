"""Exercise doctor health checks.

Doctor is byor's read-only diagnosis, so these tests pin the healthy-repo report, the documented
JSON shape, and a finding for each way an install decays: missing sgconfig or ast-grep, invalid
rules, duplicate ids, stale gate files or package mirrors, dead registry paths, drifted skill
renders, a removed ignore block, deleted git shims. It must stay graceful outside a byor repo, and
quick mode skips the recursive rule validation.
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
    write_global_check,
    write_package_check,
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
from byor.scaffold.githooks import SHIM_MARKER
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
        "ignore_block",
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


# Doctor was the only surface that merged checks without package checks, so a
# package-origin check could never be verified through it.
def test_doctor_extra_checks_includes_package_checks(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write_package_check(home, "docs", name="docs-check", run="docs-check-cmd")
    repo = make_repo(home)
    install_package(repo, "docs")
    capsys.readouterr()

    assert main(["doctor", "--repo", str(repo)]) == 0

    assert "docs-check (package:docs)" in capsys.readouterr().out


# ~/sgconfig.yml with a non-list ruleDirs cannot be repaired by `byor install`
# (it raises pointing at an init-only flag), so the remedy must say hand-edit.
def test_doctor_tells_the_user_to_hand_edit_a_malformed_home_sgconfig(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_repo(home)
    (home / "sgconfig.yml").write_text("ruleDirs: not-a-list\n")
    capsys.readouterr()

    assert main(["doctor", "--repo", str(repo)]) == 1

    out = capsys.readouterr().out
    assert "FAIL  home_sgconfig" in out
    assert "edit it by hand" in out
    assert "run `byor install`" not in out


def test_doctor_extra_checks_reports_when_all_are_excluded(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = make_repo(home)
    config = load_repo_config(repo)
    config.checks.append(CheckDef("ruff", ["py"], "uv run ruff check"))
    save_repo_config(repo, config)
    save_local_config(repo, LocalConfig(excluded_checks=["ruff"]))
    capsys.readouterr()

    assert main(["doctor", "--repo", str(repo)]) == 0

    assert "all configured checks are excluded" in capsys.readouterr().out


def test_doctor_fails_when_the_ignore_block_is_removed(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = make_repo(home)
    gitignore = repo / ".gitignore"
    gitignore.write_text("node_modules/\n")
    capsys.readouterr()

    assert main(["doctor", "--repo", str(repo)]) == 1

    out = capsys.readouterr().out
    assert "FAIL  ignore_block" in out
    assert "run `byor init` to restore it" in out
    # Doctor is read-only: it reports the missing block without rewriting it.
    assert gitignore.read_text() == "node_modules/\n"


def test_doctor_accepts_a_private_ignore_block(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = home / "repo"
    repo.mkdir()
    git(repo, "init", "--quiet")
    assert main(["init", "--repo", str(repo), "--non-interactive", "--private"]) == 0
    capsys.readouterr()

    assert main(["doctor", "--repo", str(repo)]) == 0

    assert "ok    ignore_block" in capsys.readouterr().out


def test_doctor_reports_a_deleted_git_shim(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = home / "repo"
    repo.mkdir()
    git(repo, "init", "--quiet")
    assert main(["init", "--repo", str(repo), "--non-interactive", "--git-hooks"]) == 0
    (repo / ".git" / "hooks" / "post-checkout").unlink()
    capsys.readouterr()

    assert main(["doctor", "--repo", str(repo)]) == 1

    out = capsys.readouterr().out
    assert "FAIL  git_shims" in out
    assert ".git/hooks/post-checkout is missing; run `byor init --git-hooks`" in out


def test_doctor_reports_current_git_shims_and_skips_repos_without_them(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = home / "repo"
    repo.mkdir()
    git(repo, "init", "--quiet")
    assert main(["init", "--repo", str(repo), "--non-interactive", "--git-hooks"]) == 0
    unhooked = make_repo(home, name="unhooked")
    capsys.readouterr()

    assert main(["doctor", "--repo", str(repo)]) == 0
    out = capsys.readouterr().out
    assert "ok    git_shims" in out
    assert "FAIL" not in out

    assert main(["doctor", "--repo", str(unhooked)]) == 0
    assert "git_shims" not in capsys.readouterr().out


# `pre-commit install` renames byor's gate shim to pre-commit.legacy and
# chains it, so enforcement still works. init refuses to reclaim pre-commit's
# unmarked hook (marker safety), so flagging this state as a problem could
# never converge: a current chained shim is a note, not a FAIL.
def displaced_gate_repo(home: Path) -> Path:
    repo = home / "repo"
    repo.mkdir()
    git(repo, "init", "--quiet")
    assert main(["init", "--repo", str(repo), "--non-interactive", "--private", "--gate"]) == 0
    hooks = repo / ".git" / "hooks"
    (hooks / "pre-commit").rename(hooks / "pre-commit.legacy")
    (hooks / "pre-commit").write_text("#!/bin/sh\n# generated by pre-commit\nexec pre-commit run\n")
    return repo


def test_doctor_notes_a_current_gate_shim_displaced_by_pre_commit_install(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = displaced_gate_repo(home)
    capsys.readouterr()

    assert main(["doctor", "--repo", str(repo)]) == 0

    out = capsys.readouterr().out
    assert "ok    git_shims" in out
    assert "displaced to pre-commit.legacy by `pre-commit install`" in out
    assert "still chains" in out
    assert "pre-commit uninstall" in out


def test_doctor_flags_a_drifted_displaced_gate_shim(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = displaced_gate_repo(home)
    legacy = repo / ".git" / "hooks" / "pre-commit.legacy"
    legacy.write_text(f"#!/bin/sh\n{SHIM_MARKER}\nbyor agent-check --files old-behavior\n")
    capsys.readouterr()

    assert main(["doctor", "--repo", str(repo)]) == 1

    out = capsys.readouterr().out
    assert "FAIL  git_shims" in out
    assert ".git/hooks/pre-commit.legacy is an outdated byor shim" in out
    assert "pre-commit uninstall" in out
    assert "byor init --private --gate" in out


# pre-commit runs the chained .legacy hook only when os.access(X_OK) passes
# and silently skips it otherwise, so the exec bit is part of gate health.
@pytest.mark.skipif(sys.platform == "win32", reason="os.access(X_OK) is vacuous on Windows")
def test_doctor_flags_a_non_executable_displaced_gate_shim(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = displaced_gate_repo(home)
    legacy = repo / ".git" / "hooks" / "pre-commit.legacy"
    legacy.chmod(0o644)
    capsys.readouterr()

    assert main(["doctor", "--repo", str(repo)]) == 1

    out = capsys.readouterr().out
    assert "FAIL  git_shims" in out
    assert ".git/hooks/pre-commit.legacy is not executable" in out
    assert "chmod +x .git/hooks/pre-commit.legacy" in out


def test_doctor_notes_a_user_owned_precommit_next_to_byor_sync_shims(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = home / "repo"
    repo.mkdir()
    git(repo, "init", "--quiet")
    assert main(["init", "--repo", str(repo), "--non-interactive", "--git-hooks"]) == 0
    (repo / ".git" / "hooks" / "pre-commit").write_text("#!/bin/sh\nexit 0\n")
    capsys.readouterr()

    assert main(["doctor", "--repo", str(repo)]) == 0

    out = capsys.readouterr().out
    assert "ok    git_shims" in out
    assert "pre-commit hook is user-owned; byor is not managing a commit gate here" in out


def test_doctor_stays_silent_about_a_user_precommit_when_byor_installed_no_shims(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = home / "repo"
    repo.mkdir()
    git(repo, "init", "--quiet")
    assert main(["init", "--repo", str(repo), "--non-interactive"]) == 0
    (repo / ".git" / "hooks" / "pre-commit").write_text("#!/bin/sh\nexit 0\n")
    capsys.readouterr()

    assert main(["doctor", "--repo", str(repo)]) == 0

    assert "git_shims" not in capsys.readouterr().out


def test_doctor_reports_missing_ast_grep_with_the_install_message(
    home: Path,
    # monkeypatch isolates process state (env, cwd, stdio): an external boundary
    # ast-grep-ignore: python.question-mocks
    monkeypatch: pytest.MonkeyPatch,
    *,
    capsys: pytest.CaptureFixture[str],
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


# A user-owned mirror .ignore that hides rules used to fail rules_visible
# forever: the prescribed `byor init` respected the file's ownership and
# left it byte-identical, so the remediation could never converge.
def test_doctor_rules_visible_remediation_converges_for_mirror_dirs(home: Path) -> None:
    repo = make_repo(home)
    ignore = repo / ".byor" / "rules" / "personal" / "global" / ".ignore"
    ignore.write_text("# my own ignore file\n")
    assert main(["doctor", "--repo", str(repo), "--quick"]) == 1

    assert main(["init", "--repo", str(repo), "--non-interactive"]) == 0

    assert main(["doctor", "--repo", str(repo), "--quick"]) == 0


def test_doctor_flags_duplicate_project_and_local_ids(home: Path) -> None:
    repo = make_repo(home)
    write_rule(repo / ".byor" / "rules" / "project" / "no-cast.yml", "no-cast")
    write_rule(repo / ".byor" / "rules" / "personal" / "local" / "no-cast.yml", "no-cast")

    checks = collect_checks(repo, home / "xdg" / "byor", quick=False)

    failed = {check.id: check for check in checks if not check.ok}
    assert set(failed) == {"rule_ids_unique"}
    assert "no-cast" in failed["rule_ids_unique"].message


def test_doctor_reports_a_package_id_collision_instead_of_dying(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write_package_rule(home, "pkg-a", relpath="dup.yml", rule_id="dup-id")
    write_package_rule(home, "pkg-b", relpath="dup.yml", rule_id="dup-id")
    repo = make_repo(home)
    install_package(repo, "pkg-a")
    install_package(repo, "pkg-b")
    capsys.readouterr()

    assert main(["doctor", "--repo", str(repo)]) == 1

    captured = capsys.readouterr()
    assert "FAIL  package_rules" in captured.out
    assert "byor exclude" in captured.out
    # The report still renders instead of one raw escaped error.
    assert "ok    repo_config" in captured.out
    assert captured.err == ""

    assert main(["doctor", "--repo", str(repo), "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert any(check["id"] == "package_rules" and not check["ok"] for check in payload["checks"])


def test_doctor_reports_a_corrupt_local_config_instead_of_dying(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = make_repo(home)
    (repo / ".byor" / "local.yml").write_text("version: 1\nglobal: [not, a, mapping]\n")
    capsys.readouterr()

    assert main(["doctor", "--repo", str(repo)]) == 1

    captured = capsys.readouterr()
    assert "FAIL  local_config" in captured.out
    assert "fix .byor/local.yml by hand" in captured.out
    assert "ok    repo_config" in captured.out
    assert captured.err == ""

    assert main(["doctor", "--repo", str(repo), "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert any(check["id"] == "local_config" and not check["ok"] for check in payload["checks"])


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


def test_doctor_notes_a_gate_repo_without_a_local_pre_commit_hook(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The CI leg still enforces, so a never-installed local hook is an
    # informational ok-row, not a failure.
    repo = home / "gated"
    repo.mkdir()
    git(repo, "init", "--quiet")
    assert main(["init", "--repo", str(repo), "--non-interactive", "--gate"]) == 0
    capsys.readouterr()

    assert main(["doctor", "--repo", str(repo)]) == 0

    out = capsys.readouterr().out
    assert "ok    gate_hook" in out
    assert "run `uvx pre-commit install`" in out

    # Any pre-commit hook file counts as active: the row disappears.
    (repo / ".git" / "hooks" / "pre-commit").write_text("#!/bin/sh\nexec pre-commit run\n")
    assert main(["doctor", "--repo", str(repo)]) == 0
    assert "gate_hook" not in capsys.readouterr().out


def test_doctor_says_nothing_about_the_pre_commit_hook_in_a_non_gate_repo(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_repo(home)
    capsys.readouterr()

    assert main(["doctor", "--repo", str(repo)]) == 0

    assert "gate_hook" not in capsys.readouterr().out


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


# monkeypatch isolates process state (env, cwd, stdio): an external boundary
# ast-grep-ignore: python.question-mocks
def gated_script_repo(home: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    (home / "fix.sh").write_text("#!/bin/sh\necho hi\n")
    write_global_check("fixer", "~/fix.sh")
    repo = home / "gated"
    repo.mkdir()
    git(repo, "init", "--quiet")
    assert main(["init", "--repo", str(repo), "--non-interactive", "--gate"]) == 0
    return repo


def test_doctor_flags_a_missing_vendored_script(
    home: Path,
    # monkeypatch isolates process state (env, cwd, stdio): an external boundary
    # ast-grep-ignore: python.question-mocks
    monkeypatch: pytest.MonkeyPatch,
    *,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = gated_script_repo(home, monkeypatch)
    (repo / ".byor" / "scripts" / "fix.sh").unlink()
    capsys.readouterr()

    assert main(["doctor", "--repo", str(repo)]) == 1

    out = capsys.readouterr().out
    assert "FAIL  vendored_scripts" in out
    assert ".byor/scripts/fix.sh is missing" in out
    # Doctor is read-only: the script is not restored.
    assert not (repo / ".byor" / "scripts" / "fix.sh").exists()


# The docs bless naming the interpreter in `run` instead of setting the exec
# bit; doctor used to false-FAIL that documented shape.
def test_doctor_accepts_a_non_executable_interpreter_invoked_vendored_script(
    home: Path,
    # monkeypatch isolates process state (env, cwd, stdio): an external boundary
    # ast-grep-ignore: python.question-mocks
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    (home / "lint.sh").write_text("echo hi\n")
    write_global_check("linter", "sh ~/lint.sh")
    repo = home / "gated"
    repo.mkdir()
    git(repo, "init", "--quiet")
    assert main(["init", "--repo", str(repo), "--non-interactive", "--gate"]) == 0
    vendored = repo / ".byor" / "scripts" / "lint.sh"
    vendored.chmod(0o644)

    assert main(["doctor", "--repo", str(repo)]) == 0


@pytest.mark.skipif(sys.platform == "win32", reason="os.access(X_OK) is vacuous on Windows")
def test_doctor_flags_a_non_executable_directly_invoked_vendored_script(
    home: Path,
    # monkeypatch isolates process state (env, cwd, stdio): an external boundary
    # ast-grep-ignore: python.question-mocks
    monkeypatch: pytest.MonkeyPatch,
    *,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = gated_script_repo(home, monkeypatch)
    (repo / ".byor" / "scripts" / "fix.sh").chmod(0o644)
    capsys.readouterr()

    assert main(["doctor", "--repo", str(repo)]) == 1

    out = capsys.readouterr().out
    assert "FAIL  vendored_scripts" in out
    assert "chmod +x .byor/scripts/fix.sh" in out
    # chmod alone does not survive a Windows checkout: the git index mode does.
    assert "git update-index --chmod=+x .byor/scripts/fix.sh" in out


# A vendored runner that calls a second vendored script hid that dependency
# from doctor, which only tokenized check.run: delete the helper and doctor
# stayed green while the committed gate broke.
def test_doctor_flags_a_missing_transitively_referenced_vendored_script(
    home: Path,
    # monkeypatch isolates process state (env, cwd, stdio): an external boundary
    # ast-grep-ignore: python.question-mocks
    monkeypatch: pytest.MonkeyPatch,
    *,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    scripts = home / ".config" / "byor" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "runner.sh").write_text('#!/bin/sh\nexec "${HOME}/.config/byor/scripts/helper.py" "$@"\n')
    (scripts / "helper.py").write_text("#!/usr/bin/env python3\nprint('ok')\n")
    write_global_check("runner", "~/.config/byor/scripts/runner.sh")
    repo = home / "gated"
    repo.mkdir()
    git(repo, "init", "--quiet")
    assert main(["init", "--repo", str(repo), "--non-interactive", "--gate"]) == 0
    (repo / ".byor" / "scripts" / "helper.py").unlink()
    capsys.readouterr()

    assert main(["doctor", "--repo", str(repo)]) == 1

    out = capsys.readouterr().out
    assert "FAIL  vendored_scripts" in out
    assert ".byor/scripts/helper.py is missing" in out


@pytest.mark.skipif(sys.platform == "win32", reason="os.access(X_OK) is vacuous on Windows")
def test_doctor_flags_a_non_executable_transitively_referenced_vendored_script(
    home: Path,
    # monkeypatch isolates process state (env, cwd, stdio): an external boundary
    # ast-grep-ignore: python.question-mocks
    monkeypatch: pytest.MonkeyPatch,
    *,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    scripts = home / ".config" / "byor" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "runner.sh").write_text('#!/bin/sh\nexec "${HOME}/.config/byor/scripts/helper.py" "$@"\n')
    (scripts / "helper.py").write_text("#!/usr/bin/env python3\nprint('ok')\n")
    write_global_check("runner", "~/.config/byor/scripts/runner.sh")
    repo = home / "gated"
    repo.mkdir()
    git(repo, "init", "--quiet")
    assert main(["init", "--repo", str(repo), "--non-interactive", "--gate"]) == 0
    (repo / ".byor" / "scripts" / "helper.py").chmod(0o644)
    capsys.readouterr()

    assert main(["doctor", "--repo", str(repo)]) == 1

    out = capsys.readouterr().out
    assert "FAIL  vendored_scripts" in out
    assert "chmod +x .byor/scripts/helper.py" in out
    assert "git update-index --chmod=+x .byor/scripts/helper.py" in out


def test_doctor_flags_a_drifted_vendored_script(
    home: Path,
    # monkeypatch isolates process state (env, cwd, stdio): an external boundary
    # ast-grep-ignore: python.question-mocks
    monkeypatch: pytest.MonkeyPatch,
    *,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = gated_script_repo(home, monkeypatch)
    (home / "fix.sh").write_text("#!/bin/sh\necho changed\n")
    capsys.readouterr()

    assert main(["doctor", "--repo", str(repo)]) == 1

    out = capsys.readouterr().out
    assert "FAIL  vendored_scripts" in out
    assert ".byor/scripts/fix.sh drifted from ~/fix.sh" in out
    assert "run `byor init --gate`" in out
    # Doctor is read-only: the vendored copy still has the old body.
    assert "echo hi" in (repo / ".byor" / "scripts" / "fix.sh").read_text()


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
