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
