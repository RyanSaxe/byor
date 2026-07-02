"""Exercise standalone BYOR check script behavior.

The repository vendors custom checks into `.byor/scripts`, and those checks have their own command
line contract outside the Python package. These tests keep the no-argument path honest: CI-style
invocation must scan the repo, while hook-style invocation can still pass explicit filenames.
"""

from __future__ import annotations

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


NO_SUPPRESSION_COMMAND = (sys.executable, str(SCRIPTS_DIR / "no-suppression-comments.py"))
MODULE_CONTRACT_COMMAND = (sys.executable, str(SCRIPTS_DIR / "module-contract.py"))


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
