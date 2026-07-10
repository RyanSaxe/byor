"""Exercise standalone BYOR check script behavior.

The repository vendors custom checks into `.byor/scripts`, and those checks have their own command
line contract outside the Python package. These tests keep the no-argument path honest: CI-style
invocation must scan the repo, while hook-style invocation can still pass explicit filenames.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from support import git

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / ".byor" / "scripts"
SUPPRESSION_CONTENT = "value = 1  # noqa\n"
SCRIPT_CASES: tuple[tuple[str, str, str], ...] = (
    ("module-contract.py", "module docstring required", "def missing_contract() -> int:\n    return 1\n"),
    (
        "no-thin-docstrings.py",
        "thin docstring",
        'def documented() -> int:\n    """Too thin."""\n    return 1\n',
    ),
    ("no-suppression-comments.py", "suppression comment is not allowed", SUPPRESSION_CONTENT),
)


@pytest.mark.parametrize(("script_name", "expected", "bad_content"), SCRIPT_CASES)
def test_check_script_no_args_scans_unignored_repo_files(
    tmp_path: Path,
    *,
    script_name: str,
    expected: str,
    bad_content: str,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "--quiet")
    (repo / "bad.py").write_text(bad_content)

    completed = subprocess.run(
        (sys.executable, str(SCRIPTS_DIR / script_name)),
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert "bad.py" in completed.stdout
    assert expected in completed.stdout


@pytest.mark.parametrize(("script_name", "expected", "bad_content"), SCRIPT_CASES)
def test_check_script_no_args_respects_gitignore(
    tmp_path: Path,
    *,
    script_name: str,
    expected: str,
    bad_content: str,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "--quiet")
    (repo / ".gitignore").write_text("ignored.py\n")
    (repo / "ignored.py").write_text(bad_content)

    completed = subprocess.run(
        (sys.executable, str(SCRIPTS_DIR / script_name)),
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert expected not in completed.stdout


@pytest.mark.parametrize(("script_name", "expected", "bad_content"), SCRIPT_CASES)
def test_check_script_reports_non_utf8_file_and_keeps_scanning(
    tmp_path: Path,
    *,
    script_name: str,
    expected: str,
    bad_content: str,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "--quiet")
    (repo / "latin.py").write_bytes('x = "café"\n'.encode("latin-1"))
    (repo / "bad.py").write_text(bad_content)

    completed = subprocess.run(
        (sys.executable, str(SCRIPTS_DIR / script_name)),
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert "latin.py:1: file is not valid UTF-8" in completed.stdout
    assert expected in completed.stdout


PYFILES_COMMAND = (sys.executable, str(SCRIPTS_DIR / "lib" / "pyfiles.py"))


def _pyfiles(*args: str, cwd: Path) -> list[str]:
    completed = subprocess.run(
        (*PYFILES_COMMAND, *args),
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return [entry for entry in completed.stdout.split("\0") if entry]


def test_pyfiles_echoes_back_only_python_file_arguments(tmp_path: Path) -> None:
    (tmp_path / "kept.py").write_text("x = 1\n")
    (tmp_path / "notes.txt").write_text("not python\n")

    assert _pyfiles("kept.py", "notes.txt", "missing.py", cwd=tmp_path) == ["kept.py"]


def test_pyfiles_no_args_discovers_repo_python_files(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "--quiet")
    (repo / "tracked.py").write_text("x = 1\n")
    git(repo, "add", "tracked.py")
    (repo / "untracked.py").write_text("x = 1\n")
    (repo / "notes.txt").write_text("not python\n")

    discovered = {Path(entry).name for entry in _pyfiles(cwd=repo)}

    assert discovered == {"tracked.py", "untracked.py"}


def test_pyfiles_no_args_respects_gitignore(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "--quiet")
    (repo / ".gitignore").write_text("ignored.py\n")
    (repo / "ignored.py").write_text("x = 1\n")

    assert _pyfiles(cwd=repo) == []


def test_pyfiles_nul_delimits_space_containing_filenames(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "--quiet")
    (repo / "with space.py").write_text("x = 1\n")

    assert [Path(entry).name for entry in _pyfiles(cwd=repo)] == ["with space.py"]


def test_pyfiles_no_args_discovers_newline_and_non_ascii_filenames(tmp_path: Path) -> None:
    # Newline-splitting git output mangles a newline filename, and git's
    # core.quotePath quoting turns a non-ASCII name into a dropped file.
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "--quiet")
    (repo / "模块.py").write_text("x = 1\n")
    newline_name = "line\nbreak.py"
    try:
        (repo / newline_name).write_text("x = 1\n")
    except OSError:
        pytest.skip("this filesystem cannot create a filename containing a newline")

    discovered = {Path(entry).name for entry in _pyfiles(cwd=repo)}

    assert discovered == {"模块.py", newline_name}


NO_SUPPRESSION_COMMAND = (sys.executable, str(SCRIPTS_DIR / "no-suppression-comments.py"))
MODULE_CONTRACT_COMMAND = (sys.executable, str(SCRIPTS_DIR / "module-contract.py"))


@pytest.mark.parametrize("script_name", ["module-contract.py", "no-thin-docstrings.py"])
def test_ast_check_scripts_report_files_the_interpreter_cannot_parse(tmp_path: Path, *, script_name: str) -> None:
    # Broken (or newer-than-the-interpreter) syntax must fail loudly instead
    # of silently passing the gate.
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "--quiet")
    (repo / "future.py").write_text("def broken(:\n    pass\n")

    completed = subprocess.run(
        (sys.executable, str(SCRIPTS_DIR / script_name)),
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert "future.py:1: cannot be parsed by Python" in completed.stdout


def test_no_suppression_comments_ignores_markers_inside_strings(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "--quiet")
    (repo / "strings.py").write_text('DOC = "add # noqa to the line"\n')

    completed = subprocess.run(
        NO_SUPPRESSION_COMMAND,
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert completed.stdout == ""


def test_no_suppression_comments_still_scans_broken_files(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "--quiet")
    (repo / "broken.py").write_text("x = (\n    # noqa\n")

    completed = subprocess.run(
        NO_SUPPRESSION_COMMAND,
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert "suppression comment is not allowed" in completed.stdout


RUFF_SCRIPT_COMMAND = (sys.executable, str(SCRIPTS_DIR / "ruff.py"))
EXAMPLE_RUFF_SH = Path(__file__).resolve().parents[1] / "examples" / "config" / "scripts" / "ruff.sh"


def test_example_ruff_sh_whole_repo_scan_survives_space_containing_filenames(tmp_path: Path) -> None:
    # The old unquoted $(git ls-files ...) fallback word-split "my report.py"
    # into two bogus paths, failing the check on a clean repo.
    sh = shutil.which("sh")
    if sh is None:
        pytest.skip("running the example check script requires an `sh` on PATH")
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "--quiet")
    (repo / "ruff.toml").write_text("")
    (repo / "my report.py").write_text("x = 1\n")

    completed = subprocess.run(
        (sh, str(EXAMPLE_RUFF_SH)),
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stdout == ""


def test_example_ruff_sh_reports_an_unused_import_without_deleting_it(tmp_path: Path) -> None:
    # The fix pass must never autofix F401: at hook time it deleted a
    # just-added import before the agent's next edit added its usage.
    sh = shutil.which("sh")
    if sh is None:
        pytest.skip("running the example check script requires an `sh` on PATH")
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "--quiet")
    (repo / "ruff.toml").write_text("")
    content = "import os\n"
    (repo / "code.py").write_text(content)

    completed = subprocess.run(
        (sh, str(EXAMPLE_RUFF_SH)),
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert "F401" in completed.stdout
    assert (repo / "code.py").read_text() == content


EXAMPLE_SCRIPTS = Path(__file__).resolve().parents[1] / "examples" / "config" / "scripts"


def _run_example_script(script: str, *args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    sh = shutil.which("sh")
    if sh is None:
        pytest.skip("running the example check script requires an `sh` on PATH")
    return subprocess.run(
        (sh, str(EXAMPLE_SCRIPTS / script), *args),
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )


DEPENDENCIES_BLOCK = 'dependencies = [\n    "httpx>=0.27",\n]\n'


def _committed_pyproject_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "--quiet")
    (repo / "pyproject.toml").write_text(f'[project]\nname = "demo"\nversion = "0.1.0"\n{DEPENDENCIES_BLOCK}')
    git(repo, "add", "pyproject.toml")
    git(repo, "commit", "--quiet", "-m", "init")
    return repo


def test_dependency_gate_passes_when_dependencies_match_the_last_commit(tmp_path: Path) -> None:
    repo = _committed_pyproject_repo(tmp_path)

    completed = _run_example_script("dependency-gate.sh", "pyproject.toml", cwd=repo)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stdout == ""


def test_dependency_gate_fails_when_a_dependency_is_added_or_removed(tmp_path: Path) -> None:
    repo = _committed_pyproject_repo(tmp_path)
    pyproject = repo / "pyproject.toml"

    added = pyproject.read_text().replace('"httpx>=0.27",', '"httpx>=0.27",\n    "requests>=2.31",')
    pyproject.write_text(added)
    completed = _run_example_script("dependency-gate.sh", "pyproject.toml", cwd=repo)
    assert completed.returncode == 1
    assert "ask the user first" in completed.stdout

    removed = pyproject.read_text().replace(DEPENDENCIES_BLOCK, "dependencies = []\n")
    pyproject.write_text(removed)
    completed = _run_example_script("dependency-gate.sh", "pyproject.toml", cwd=repo)
    assert completed.returncode == 1


def test_dependency_gate_ignores_changes_outside_the_dependency_list(tmp_path: Path) -> None:
    repo = _committed_pyproject_repo(tmp_path)
    pyproject = repo / "pyproject.toml"
    pyproject.write_text(pyproject.read_text().replace('version = "0.1.0"', 'version = "0.2.0"'))

    completed = _run_example_script("dependency-gate.sh", "pyproject.toml", cwd=repo)

    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_dependency_gate_passes_quietly_without_history_to_compare(tmp_path: Path) -> None:
    # A repo with no commits and a directory that is not a repo at all both
    # have nothing to diff against; the check must not block either.
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    git(fresh, "init", "--quiet")
    (fresh / "pyproject.toml").write_text(f"[project]\n{DEPENDENCIES_BLOCK}")
    assert _run_example_script("dependency-gate.sh", "pyproject.toml", cwd=fresh).returncode == 0

    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "pyproject.toml").write_text(f"[project]\n{DEPENDENCIES_BLOCK}")
    assert _run_example_script("dependency-gate.sh", "pyproject.toml", cwd=plain).returncode == 0


def test_uv_lock_guard_fails_only_on_a_hand_edited_lockfile(tmp_path: Path) -> None:
    assert _run_example_script("uv-lock-guard.sh", "uv.lock", cwd=tmp_path).returncode == 1
    completed = _run_example_script("uv-lock-guard.sh", "sub/uv.lock", cwd=tmp_path)
    assert completed.returncode == 1
    assert "uv add" in completed.stdout

    assert _run_example_script("uv-lock-guard.sh", "poetry.lock", cwd=tmp_path).returncode == 0
    assert _run_example_script("uv-lock-guard.sh", cwd=tmp_path).returncode == 0


GOOD_MODULE_DOCSTRING = (
    '"""Serve as a fixture module for the module contract tests.\n'
    "\n"
    "The detail paragraph documents enough words and sentences to satisfy the\n"
    "module contract check. It exists only so each test can exercise one rule\n"
    "at a time without unrelated findings.\n"
    '"""\n'
)
FIXTURE_PYPROJECT = '[project]\nname = "fixture"\nrequires-python = ">=3.10"\n'


def test_module_contract_accepts_pep723_script_inside_repo_without_pyproject(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "--quiet")
    pep723_header = '# /// script\n# requires-python = ">=3.10"\n# ///\n'
    (repo / "script.py").write_text(pep723_header + GOOD_MODULE_DOCSTRING)

    completed = subprocess.run(
        MODULE_CONTRACT_COMMAND,
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert completed.stdout == ""


def test_module_contract_accepts_try_except_import_fallback_in_all(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "--quiet")
    package = repo / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    module = package / "module.py"
    module.write_text(
        GOOD_MODULE_DOCSTRING
        + "\ntry:\n    import tomllib\nexcept ImportError:\n    import tomli as tomllib\n\n"
        + '__all__ = ("tomllib",)\n'
    )

    completed = subprocess.run(
        (*MODULE_CONTRACT_COMMAND, str(module)),
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert completed.stdout == ""


def test_module_contract_reports_all_missing_public_definition(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "--quiet")
    package = repo / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    module = package / "module.py"
    module.write_text(GOOD_MODULE_DOCSTRING + "\n__all__ = ()\n\n\ndef exported() -> int:\n    return 1\n")

    completed = subprocess.run(
        (*MODULE_CONTRACT_COMMAND, str(module)),
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert "__all__ is missing public definitions" in completed.stdout
    assert "exported" in completed.stdout


def test_module_contract_summary_abbreviation_is_one_sentence(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "--quiet")
    (repo / "pyproject.toml").write_text(FIXTURE_PYPROJECT)
    module = repo / "module.py"
    summary = '"""Handle repo scans, e.g. git repositories and plain directories.\n'
    module.write_text(summary + GOOD_MODULE_DOCSTRING.split("\n", maxsplit=1)[1])

    completed = subprocess.run(
        (*MODULE_CONTRACT_COMMAND, str(module)),
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert completed.stdout == ""


def test_module_contract_single_line_docstring_is_one_finding(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "--quiet")
    (repo / "pyproject.toml").write_text(FIXTURE_PYPROJECT)
    module = repo / "module.py"
    module.write_text('"""Too short."""\n')

    completed = subprocess.run(
        (*MODULE_CONTRACT_COMMAND, str(module)),
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert "blank line after the summary" in completed.stdout
    assert len(completed.stdout.splitlines()) == 1


def _ruff_workspace(tmp_path: Path, *, content: str) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    # An empty ruff.toml pins config discovery to the workspace so the
    # user-level fallback config cannot change what these tests observe.
    (workspace / "ruff.toml").write_text("")
    (workspace / "code.py").write_text(content)
    return workspace


def test_ruff_script_passes_clean_file(tmp_path: Path) -> None:
    workspace = _ruff_workspace(tmp_path, content="x = 1\n")

    completed = subprocess.run(
        RUFF_SCRIPT_COMMAND,
        cwd=workspace,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert completed.stdout == ""


def test_ruff_script_reports_unfixable_violation(tmp_path: Path) -> None:
    workspace = _ruff_workspace(tmp_path, content="print(undefined_name)\n")

    completed = subprocess.run(
        RUFF_SCRIPT_COMMAND,
        cwd=workspace,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert "Remaining ruff issues to fix:" in completed.stdout
    assert "F821" in completed.stdout


def test_ruff_script_autofixes_but_still_exits_nonzero(tmp_path: Path) -> None:
    # CI runs this check in a throwaway workspace, so an autofix must still
    # fail the gate; the report explains why the file changed.
    workspace = _ruff_workspace(tmp_path, content="x = 1;\n")

    completed = subprocess.run(
        RUFF_SCRIPT_COMMAND,
        cwd=workspace,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert "Autofixed by ruff (no action needed):" in completed.stdout
    assert (workspace / "code.py").read_text() == "x = 1\n"


def test_ruff_script_fails_loudly_when_ruff_cannot_run(tmp_path: Path) -> None:
    workspace = _ruff_workspace(tmp_path, content="x = 1\n")
    (workspace / "ruff.toml").write_text("[[[ broken\n")

    completed = subprocess.run(
        RUFF_SCRIPT_COMMAND,
        cwd=workspace,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert "ruff failed to run" in completed.stdout
    assert "ruff.toml" in completed.stdout


def test_ruff_script_flags_dynamic_execution_regardless_of_project_config(tmp_path: Path) -> None:
    # The empty ruff.toml selects no project rules, so eval only surfaces
    # because byor pins S307 in its always-on pass instead of an ast-grep rule.
    workspace = _ruff_workspace(tmp_path, content='source = "1"\nresult = eval(source)\n')

    completed = subprocess.run(
        RUFF_SCRIPT_COMMAND,
        cwd=workspace,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert "S307" in completed.stdout


def test_ruff_script_flags_excess_nesting(tmp_path: Path) -> None:
    # PLR1702 is preview-only, so its own pass proves the guard-clause depth
    # limit fires even when the project selects no rules of its own.
    content = (
        "def deep(a, b, c, d):\n"
        "    if a:\n"
        "        for x in b:\n"
        "            if c:\n"
        "                while d:\n"
        "                    return x\n"
    )
    workspace = _ruff_workspace(tmp_path, content=content)

    completed = subprocess.run(
        RUFF_SCRIPT_COMMAND,
        cwd=workspace,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert "PLR1702" in completed.stdout
