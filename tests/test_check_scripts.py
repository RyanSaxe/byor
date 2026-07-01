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
SUPPRESSION_CONTENT = "value = 1  " + "#" + " noqa\n"
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


RUFF_SCRIPT_COMMAND = (sys.executable, str(SCRIPTS_DIR / "ruff.py"))


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

    assert completed.returncode == 2
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

    assert completed.returncode == 2
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
