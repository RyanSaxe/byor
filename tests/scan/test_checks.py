"""Merge precedence, exclusion, and execution for extra checks.

Effective checks merge by name with repo above package above global, and local exclusions can
disable one by name or tag. Execution details each get a case: extension filtering (extensionless
means every file), a leading tilde expanding to home, and missing or uninvocable commands warning
without crashing the scan.
"""

import shlex
import sys
from pathlib import Path

import pytest

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

    effective = effective_checks(repo, global_config, local_config=LocalConfig())

    by_name = {check.name: check for check in effective}
    assert _names(effective) == ["ruff", "mypy"]
    assert by_name["ruff"].definition.run == "repo-run"
    assert by_name["ruff"].origin == "repo"
    assert by_name["mypy"].origin == "global"


def test_package_check_sits_between_repo_and_global_by_precedence() -> None:
    repo = RepoConfig(checks=[CheckDef("ruff", ["py"], "repo-run")])
    global_config = GlobalConfig(checks=[CheckDef("mypy", ["py"], "global-run")])
    package_checks = [
        ("package:web", CheckDef("ruff", ["py"], "pkg-run")),
        ("package:web", CheckDef("eslint", ["js"], "pkg-run")),
    ]

    effective = effective_checks(repo, global_config, local_config=LocalConfig(), package_checks=package_checks)

    by_name = {check.name: check for check in effective}
    assert _names(effective) == ["ruff", "eslint", "mypy"]
    # repo wins the name it shares with the package; the package keeps eslint.
    assert by_name["ruff"].origin == "repo"
    assert by_name["ruff"].definition.run == "repo-run"
    assert by_name["eslint"].origin == "package:web"


def test_local_exclusion_disables_a_package_check() -> None:
    package_checks = [("package:web", CheckDef("eslint", ["js"], "run"))]
    local = LocalConfig(excluded_checks=["eslint"])

    effective = effective_checks(RepoConfig(), GlobalConfig(), local_config=local, package_checks=package_checks)

    assert _names(effective) == []


def test_local_exclusion_disables_a_check_by_name() -> None:
    repo = RepoConfig(checks=[CheckDef("ruff", ["py"], "run"), CheckDef("eslint", ["js"], "run")])
    local = LocalConfig(excluded_checks=["eslint"])

    effective = effective_checks(repo, GlobalConfig(), local_config=local)

    assert _names(effective) == ["ruff"]


def test_local_exclusion_disables_a_check_by_tag() -> None:
    repo = RepoConfig(
        checks=[
            CheckDef("ruff", ["py"], "run", tags=["format"]),
            CheckDef("ty", ["py"], "run", tags=["strict"]),
        ]
    )
    local = LocalConfig(excluded_check_tags=["strict"])

    effective = effective_checks(repo, GlobalConfig(), local_config=local)

    assert _names(effective) == ["ruff"]


def test_extensionless_check_runs_when_no_extensions_listed(tmp_path: Path) -> None:
    run = shlex.join([sys.executable, "-c", "pass"])
    check = EffectiveCheck(CheckDef("anyfile", [], run), origin="repo")
    target = tmp_path / "notes.txt"
    target.write_text("hello\n")

    outcome = run_checks([check], tmp_path, files=[target])

    assert outcome.failures == []
    assert outcome.warnings == []


def test_failing_check_appends_a_named_section_with_raw_output(tmp_path: Path) -> None:
    script = tmp_path / "fail.py"
    script.write_text(
        "import sys\nprint('stdout complaint')\nprint('stderr complaint', file=sys.stderr)\nsys.exit(1)\n"
    )
    check = EffectiveCheck(
        CheckDef("strict", ["py"], shlex.join([sys.executable, str(script)])),
        origin="repo",
    )
    target = tmp_path / "src.py"
    target.write_text("x = 1\n")

    outcome = run_checks([check], tmp_path, files=[target])

    assert len(outcome.failures) == 1
    section = outcome.failures[0]
    assert section.startswith("### strict\n")
    assert "stdout complaint" in section
    assert "stderr complaint" in section


def test_check_skips_files_whose_extension_does_not_match(tmp_path: Path) -> None:
    check = EffectiveCheck(
        CheckDef(
            "py-only",
            ["py"],
            shlex.join([sys.executable, "-c", "import sys; sys.exit(1)"]),
        ),
        origin="repo",
    )
    only_js = tmp_path / "app.js"
    only_js.write_text("//\n")

    outcome = run_checks([check], tmp_path, files=[only_js])

    assert outcome.failures == []


# monkeypatch isolates process state (env, cwd, stdio): an external boundary
# ast-grep-ignore: python.question-mocks
def test_run_command_expands_a_leading_tilde_to_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    scripts = home / ".config" / "byor" / "scripts"
    scripts.mkdir(parents=True)
    script = scripts / "check.py"
    script.write_text("print('ran from home'); raise SystemExit(1)\n")
    # os.path.expanduser reads HOME on POSIX and USERPROFILE on Windows.
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    run = f"{shlex.quote(sys.executable)} ~/.config/byor/scripts/check.py"
    check = EffectiveCheck(CheckDef("tilde", ["py"], run), origin="global")
    target = tmp_path / "src.py"
    target.write_text("x = 1\n")

    outcome = run_checks([check], tmp_path, files=[target])

    assert outcome.warnings == []
    assert len(outcome.failures) == 1
    assert "ran from home" in outcome.failures[0]


def test_missing_command_warns_once_and_does_not_fail(tmp_path: Path) -> None:
    check = EffectiveCheck(
        CheckDef("ghost", ["py"], "this-command-does-not-exist --flag"),
        origin="repo",
    )
    target = tmp_path / "src.py"
    target.write_text("x = 1\n")

    outcome = run_checks([check], tmp_path, files=[target])

    assert outcome.failures == []
    assert len(outcome.warnings) == 1
    assert "ghost" in outcome.warnings[0]


def test_uninvocable_command_warns_and_does_not_crash(tmp_path: Path) -> None:
    # argv[0] is a directory, so exec raises an OSError other than FileNotFound.
    not_executable = tmp_path / "adir"
    not_executable.mkdir()
    check = EffectiveCheck(CheckDef("dir", ["py"], shlex.join([str(not_executable)])), origin="repo")
    target = tmp_path / "src.py"
    target.write_text("x = 1\n")

    outcome = run_checks([check], tmp_path, files=[target])

    assert outcome.failures == []
    assert len(outcome.warnings) == 1
    assert "dir" in outcome.warnings[0]
