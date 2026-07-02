"""Exercise ast-grep executable resolution and parsing.

Resolution prefers an explicitly configured command, honors the env override ahead of PATH, prefers
ast-grep over sg while skipping candidates that are not really ast-grep, and falls back to the
interpreter's bin dir for the bundled install; a missing binary must raise the exact install
message. Scan parsing is pinned too: matches carry byor metadata, multi-line matches report their
end line, and failures surface ast-grep's own message.
"""

import sys
from pathlib import Path

import pytest

from byor.errors import AstGrepNotFoundError, ByorError
from byor.scan.astgrep import (
    NOT_FOUND_MESSAGE,
    VERSION_PATTERN,
    ast_grep_version,
    resolve_ast_grep,
    scan_files,
)

# These tests run `#!/bin/sh` stand-in executables, which Windows cannot exec;
# the real-ast-grep cases below still exercise the resolver there.
requires_sh = pytest.mark.skipif(sys.platform == "win32", reason="fake executables are POSIX shell scripts")


def fake_executable(path: Path, *, script: str = 'echo "ast-grep 9.9.9"') -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\n{script}\n")
    path.chmod(0o755)
    return path


@pytest.fixture
# monkeypatch isolates process state (env, cwd, stdio): an external boundary
# ast-grep-ignore: python.question-mocks
def bin_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Provide an initially empty PATH with $BYOR_AST_GREP unset.

    The interpreter dir (the auto-mode fallback) is pointed at the same empty
    dir, so resolution sees only what a test puts here — not the real bundled
    ast-grep that lives beside the test runner's interpreter.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.delenv("BYOR_AST_GREP", raising=False)
    monkeypatch.setattr(sys, "executable", str(bin_dir / "python"))
    return bin_dir


@requires_sh
# monkeypatch isolates process state (env, cwd, stdio): an external boundary
# ast-grep-ignore: python.question-mocks
def test_env_override_wins_over_path(bin_dir: Path, monkeypatch: pytest.MonkeyPatch, *, tmp_path: Path) -> None:
    fake_executable(bin_dir / "ast-grep")
    override = fake_executable(tmp_path / "elsewhere" / "my-sg")
    monkeypatch.setenv("BYOR_AST_GREP", str(override))

    assert resolve_ast_grep() == override


@requires_sh
def test_path_resolution_prefers_ast_grep_over_sg(bin_dir: Path) -> None:
    sg = fake_executable(bin_dir / "sg")

    assert resolve_ast_grep() == sg

    ast_grep = fake_executable(bin_dir / "ast-grep")
    assert resolve_ast_grep() == ast_grep


@requires_sh
def test_resolution_skips_a_candidate_that_is_not_ast_grep(bin_dir: Path) -> None:
    # Ubuntu's /usr/bin/sg is the setgroups tool: --version is not ast-grep's.
    fake_executable(bin_dir / "sg", script='echo "sg from util-linux"')

    with pytest.raises(AstGrepNotFoundError) as excinfo:
        resolve_ast_grep()

    assert str(excinfo.value) == NOT_FOUND_MESSAGE


@requires_sh
def test_resolution_falls_through_to_a_real_ast_grep(bin_dir: Path) -> None:
    fake_executable(bin_dir / "sg", script="exit 1")
    ast_grep = fake_executable(bin_dir / "ast-grep")

    assert resolve_ast_grep() == ast_grep


@requires_sh
@pytest.mark.usefixtures("bin_dir")
def test_resolution_falls_back_to_the_interpreter_dir_when_not_on_path(
    # monkeypatch isolates process state (env, cwd, stdio): an external boundary
    # ast-grep-ignore: python.question-mocks
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    venv_bin = tmp_path / "venv-bin"
    bundled = fake_executable(venv_bin / "ast-grep")
    monkeypatch.setattr(sys, "executable", str(venv_bin / "python"))  # not on PATH

    assert resolve_ast_grep() == bundled


@requires_sh
def test_env_override_is_honored_or_fails_without_path_fallthrough(
    bin_dir: Path,
    # monkeypatch isolates process state (env, cwd, stdio): an external boundary
    # ast-grep-ignore: python.question-mocks
    monkeypatch: pytest.MonkeyPatch,
    *,
    tmp_path: Path,
) -> None:
    # $BYOR_AST_GREP wins when set; a non-ast-grep override does
    # not silently fall through to a valid ast-grep on PATH.
    fake_executable(bin_dir / "ast-grep")
    override = fake_executable(tmp_path / "elsewhere" / "sg", script="exit 1")
    monkeypatch.setenv("BYOR_AST_GREP", str(override))

    with pytest.raises(AstGrepNotFoundError):
        resolve_ast_grep()


@requires_sh
def test_configured_command_is_used_exactly(bin_dir: Path, tmp_path: Path) -> None:
    fake_executable(bin_dir / "ast-grep")
    configured = fake_executable(tmp_path / "custom" / "ast-grep")

    assert resolve_ast_grep(command=str(configured)) == configured

    with pytest.raises(AstGrepNotFoundError):
        resolve_ast_grep(command=str(tmp_path / "custom" / "missing"))


@pytest.mark.usefixtures("bin_dir")
def test_missing_executable_raises_the_exact_install_message() -> None:
    with pytest.raises(AstGrepNotFoundError) as excinfo:
        resolve_ast_grep()

    assert str(excinfo.value) == NOT_FOUND_MESSAGE
    assert "brew install ast-grep" in NOT_FOUND_MESSAGE


@requires_sh
def test_version_is_parsed_from_version_output(bin_dir: Path) -> None:
    executable = fake_executable(bin_dir / "ast-grep")

    assert ast_grep_version(executable) == "9.9.9"


def test_version_of_the_real_ast_grep_is_readable() -> None:
    version = ast_grep_version(resolve_ast_grep())

    assert VERSION_PATTERN.fullmatch(version)


@requires_sh
def test_unreadable_version_fails_cleanly(bin_dir: Path) -> None:
    broken = fake_executable(bin_dir / "ast-grep", script="exit 1")

    with pytest.raises(AstGrepNotFoundError, match="could not read an ast-grep version"):
        ast_grep_version(broken)


CAST_RULE = """\
id: no-python-cast
language: Python
severity: warning
message: Avoid typing.cast in Python code.
rule:
  pattern: cast($TYPE, $VALUE)
metadata:
  byor:
    agent_prompt: Fix the type by narrowing instead.
"""


def ast_grep_project(root: Path, rule: str = CAST_RULE) -> Path:
    (root / "sgconfig.yml").write_text("ruleDirs:\n  - rules\n")
    (root / "rules").mkdir()
    (root / "rules" / "rule.yml").write_text(rule)
    return root


def test_scan_parses_matches_with_metadata(tmp_path: Path) -> None:
    project = ast_grep_project(tmp_path)
    (project / "src.py").write_text('x = cast(int, "1")\n')

    result = scan_files(resolve_ast_grep(), project, files=[project / "src.py"])

    assert result.warnings == ""
    (match,) = result.matches
    assert match.file == str(project / "src.py")
    # 1-based; ast-grep's 0-based JSON is normalized at the parse.
    assert (match.line, match.column, match.end_line) == (1, 5, 1)
    assert match.rule_id == "no-python-cast"
    assert match.severity == "warning"
    assert match.message == "Avoid typing.cast in Python code."
    assert match.lines == 'x = cast(int, "1")'
    assert match.agent_prompt == "Fix the type by narrowing instead."


def test_scan_reports_the_end_line_of_a_multi_line_match(tmp_path: Path) -> None:
    project = ast_grep_project(tmp_path)
    (project / "src.py").write_text('pad = 0\nx = cast(\n    int,\n    "1",\n)\n')

    result = scan_files(resolve_ast_grep(), project, files=[project / "src.py"])

    (match,) = result.matches
    assert (match.line, match.end_line) == (2, 5)


def test_scan_without_metadata_yields_no_agent_prompt(tmp_path: Path) -> None:
    rule = CAST_RULE.split("metadata:\n", maxsplit=1)[0]
    project = ast_grep_project(tmp_path, rule=rule)
    (project / "src.py").write_text("x = cast(int, 1)\n")

    result = scan_files(resolve_ast_grep(), project, files=[project / "src.py"])

    assert result.matches[0].agent_prompt is None


def test_scan_failure_raises_with_ast_grep_message(tmp_path: Path) -> None:
    (tmp_path / "sgconfig.yml").write_text("ruleDirs: 5\n")
    (tmp_path / "src.py").write_text("x = 1\n")

    with pytest.raises(ByorError, match="scan` failed"):
        scan_files(resolve_ast_grep(), tmp_path, files=[tmp_path / "src.py"])
