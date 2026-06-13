"""Merge precedence, exclusion, and execution for extra checks."""

import shlex
import sys
from pathlib import Path

from byor.config import CheckDef, GlobalConfig, LocalConfig, RepoConfig
from byor.scan.checks import EffectiveCheck, effective_checks, run_checks


def _names(checks: list[EffectiveCheck]) -> list[str]:
    return [check.name for check in checks]


def test_repo_check_wins_over_global_check_of_the_same_name() -> None:
    repo = RepoConfig(checks=[CheckDef("ruff", ["py"], "repo-run")])
    global_config = GlobalConfig(
        checks=[
            CheckDef("ruff", ["py"], "global-run"),
            CheckDef("mypy", ["py"], "mypy-run"),
        ]
    )

    effective = effective_checks(repo, global_config, LocalConfig())

    by_name = {check.name: check for check in effective}
    assert _names(effective) == ["ruff", "mypy"]
    assert by_name["ruff"].definition.run == "repo-run"
    assert by_name["ruff"].origin == "repo"
    assert by_name["mypy"].origin == "global"


def test_local_exclusion_disables_a_check_by_name() -> None:
    repo = RepoConfig(
        checks=[CheckDef("ruff", ["py"], "run"), CheckDef("eslint", ["js"], "run")]
    )
    local = LocalConfig(excluded_checks=["eslint"])

    effective = effective_checks(repo, GlobalConfig(), local)

    assert _names(effective) == ["ruff"]


def test_extensionless_check_runs_when_no_extensions_listed(tmp_path: Path) -> None:
    check = _passing_check("anyfile", extensions=[])
    target = tmp_path / "notes.txt"
    target.write_text("hello\n")

    outcome = run_checks([check], tmp_path, [target])

    assert outcome.failures == []
    assert outcome.warnings == []


def test_failing_check_appends_a_named_section_with_raw_output(tmp_path: Path) -> None:
    script = tmp_path / "fail.py"
    script.write_text(
        "import sys\n"
        "print('stdout complaint')\n"
        "print('stderr complaint', file=sys.stderr)\n"
        "sys.exit(1)\n"
    )
    check = _check("strict", ["py"], shlex.join([sys.executable, str(script)]))
    target = tmp_path / "src.py"
    target.write_text("x = 1\n")

    outcome = run_checks([check], tmp_path, [target])

    assert len(outcome.failures) == 1
    section = outcome.failures[0]
    assert section.startswith("### strict\n")
    assert "stdout complaint" in section
    assert "stderr complaint" in section


def test_check_skips_files_whose_extension_does_not_match(tmp_path: Path) -> None:
    check = _check(
        "py-only", ["py"], shlex.join([sys.executable, "-c", "import sys; sys.exit(1)"])
    )
    only_js = tmp_path / "app.js"
    only_js.write_text("//\n")

    outcome = run_checks([check], tmp_path, [only_js])

    assert outcome.failures == []


def test_missing_command_warns_once_and_does_not_fail(tmp_path: Path) -> None:
    check = _check("ghost", ["py"], "this-command-does-not-exist --flag")
    target = tmp_path / "src.py"
    target.write_text("x = 1\n")

    outcome = run_checks([check], tmp_path, [target])

    assert outcome.failures == []
    assert len(outcome.warnings) == 1
    assert "ghost" in outcome.warnings[0]


def _check(name: str, extensions: list[str], run: str) -> EffectiveCheck:
    return EffectiveCheck(CheckDef(name, extensions, run), origin="repo")


def _passing_check(name: str, extensions: list[str]) -> EffectiveCheck:
    return _check(name, extensions, shlex.join([sys.executable, "-c", "pass"]))
