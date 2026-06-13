import json
import shutil
from pathlib import Path

import pytest
from conftest import make_repo, write_rule

from byor.astgrep import NOT_FOUND_MESSAGE
from byor.cli import main
from byor.config import (
    CheckDef,
    LocalConfig,
    load_repo_config,
    save_local_config,
    save_repo_config,
)
from byor.doctor import collect_checks


def doctor(repo: Path, *extra: str) -> int:
    return main(["doctor", "--repo", str(repo), *extra])


def test_doctor_reports_ok_for_a_healthy_repo(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_repo(home)
    capsys.readouterr()

    assert doctor(repo) == 0

    out = capsys.readouterr().out
    assert "ok    ast_grep_found" in out
    assert "FAIL" not in out


def test_doctor_json_matches_the_spec_shape(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_repo(home)
    capsys.readouterr()

    assert doctor(repo, "--json") == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    first = payload["checks"][0]
    assert first["id"] == "ast_grep_found"
    assert first["ok"] is True
    assert first["message"].startswith("ast-grep ")
    assert {check["id"] for check in payload["checks"]} == {
        "ast_grep_found",
        "repo_config",
        "sgconfig",
        "rule_dirs",
        "rules_visible",
        "rules_valid",
        "rule_ids_unique",
        "sync_fresh",
        "registered_repos",
        "agent_files",
    }


def test_doctor_surfaces_configured_checks_with_origin(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_repo(home)
    config = load_repo_config(repo)
    config.checks.append(CheckDef("ruff", ["py"], "uv run ruff check"))
    save_repo_config(repo, config)
    capsys.readouterr()

    assert doctor(repo) == 0

    out = capsys.readouterr().out
    assert "extra_checks" in out
    assert "checks: ruff (repo)" in out


def test_doctor_extra_checks_reports_when_all_are_excluded(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_repo(home)
    config = load_repo_config(repo)
    config.checks.append(CheckDef("ruff", ["py"], "uv run ruff check"))
    save_repo_config(repo, config)
    save_local_config(repo, LocalConfig(excluded_checks=["ruff"]))
    capsys.readouterr()

    assert doctor(repo) == 0

    assert "all configured checks are excluded" in capsys.readouterr().out


def test_doctor_reports_missing_ast_grep_with_the_install_message(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_repo(home)
    empty_bin = home / "empty-bin"
    empty_bin.mkdir()
    monkeypatch.setenv("PATH", str(empty_bin))
    monkeypatch.delenv("BYOR_AST_GREP", raising=False)
    capsys.readouterr()

    assert doctor(repo) == 1

    assert NOT_FOUND_MESSAGE in capsys.readouterr().out


def test_doctor_flags_an_uninitialized_repo(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = home / "untouched"
    repo.mkdir()

    assert doctor(repo) == 1

    out = capsys.readouterr().out
    assert "FAIL  repo_config" in out
    assert "byor init" in out


def test_doctor_flags_missing_sgconfig(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_repo(home)
    (repo / "sgconfig.yml").unlink()
    capsys.readouterr()

    assert doctor(repo) == 1

    assert "sgconfig.yml is missing; run `byor init`" in capsys.readouterr().out


def test_doctor_renders_a_failing_check_for_invalid_rules(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A broken rule stops the self-heal preamble's sync, but doctor must still
    render its check table rather than abort."""
    repo = make_repo(home)
    broken = repo / ".byor" / "rules" / "project" / "broken.yml"
    broken.write_text("id: broken\n")
    capsys.readouterr()

    assert doctor(repo) == 1

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


def test_doctor_flags_a_missing_rule_visibility_file(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Without the .ignore negations, ast-grep would skip the git-ignored rules.

    Uses the local dir: the global mirror's .ignore is restored by the
    self-heal preamble before doctor runs.
    """
    repo = make_repo(home)
    (repo / ".byor" / "rules" / "personal" / "local" / ".ignore").unlink()
    capsys.readouterr()

    assert doctor(repo, "--quick") == 1

    out = capsys.readouterr().out
    assert "FAIL  rules_visible" in out
    assert ".byor/rules/personal/local" in out


def test_doctor_flags_duplicate_project_and_local_ids(home: Path) -> None:
    repo = make_repo(home)
    write_rule(repo / ".byor" / "rules" / "project" / "no-cast.yml", "no-cast")
    write_rule(
        repo / ".byor" / "rules" / "personal" / "local" / "no-cast.yml", "no-cast"
    )

    checks = collect_checks(repo, home / "xdg" / "byor", quick=False)

    failed = {check.id: check for check in checks if not check.ok}
    assert set(failed) == {"rule_ids_unique"}
    assert "no-cast" in failed["rule_ids_unique"].message


def test_doctor_flags_registered_repos_whose_path_is_gone(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_repo(home)
    gone = make_repo(home, "gone")
    shutil.rmtree(gone)
    capsys.readouterr()

    assert doctor(repo) == 1

    assert f"{gone} no longer exists" in capsys.readouterr().out


def test_doctor_flags_missing_agent_files(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_repo(home)  # the skill render is the default agent file
    (repo / ".agents" / "skills" / "byor" / "SKILL.md").unlink()
    capsys.readouterr()

    assert doctor(repo, "--quick") == 1

    assert ".agents/skills/byor/SKILL.md is missing" in capsys.readouterr().out
