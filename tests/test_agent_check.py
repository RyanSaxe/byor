"""`byolsp agent-check` against the real ast-grep binary (SPEC 15.9)."""

import json
import re
from pathlib import Path

import pytest
from conftest import make_repo

from byolsp.cli import main

CAST_PROMPT = (
    "Do not use typing.cast here. Fix the type by narrowing, changing the "
    "signature, introducing a protocol, or restructuring the value flow."
)

# The SPEC 11.1 worked example, with agent_prompt folded to one line.
CAST_RULE = """\
id: no-python-cast
language: Python
severity: warning
message: Avoid typing.cast in Python code.
rule:
  pattern: cast($TYPE, $VALUE)
metadata:
  byolsp:
    agent_prompt: >
      Do not use typing.cast here. Fix the type by narrowing, changing the
      signature, introducing a protocol, or restructuring the value flow.
    allow_with_comment: true
"""

# No metadata, so the instruction falls back to the message; error severity
# makes ast-grep itself exit nonzero, which must still render as exit 2.
PRINT_RULE = """\
id: no-print
language: Python
severity: error
message: Avoid print in library code.
rule:
  pattern: print($$$ARGS)
"""


def make_check_repo(home: Path) -> Path:
    repo = make_repo(home)
    project = repo / ".byolsp" / "rules" / "project"
    (project / "no-python-cast.yml").write_text(CAST_RULE)
    (project / "no-print.yml").write_text(PRINT_RULE)
    return repo


def check(repo: Path, *extra: str) -> int:
    return main(["agent-check", "--repo", str(repo), *extra])


def test_clean_files_exit_zero_with_no_output(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_check_repo(home)
    source = repo / "src.py"
    source.write_text("x = 1\n")

    capsys.readouterr()

    assert check(repo, "--files", str(source)) == 0

    assert capsys.readouterr().out == ""


def test_one_diagnostic_renders_the_spec_block(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_check_repo(home)
    source = repo / "src.py"
    source.write_text('from typing import cast\nx = cast(int, "1")\n')

    capsys.readouterr()

    assert check(repo, "--files", str(source)) == 2

    assert capsys.readouterr().out == (
        "BYOLSP found 1 issue in AI-written code.\n"
        "\n"
        "src.py:2:5\n"
        "Rule: no-python-cast\n"
        "Severity: warning\n"
        "Message: Avoid typing.cast in Python code.\n"
        'Code: x = cast(int, "1")\n'
        "\n"
        "Instruction:\n"
        f"{CAST_PROMPT}\n"
    )


def test_instruction_falls_back_to_message(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_check_repo(home)
    source = repo / "src.py"
    source.write_text('print("hi")\n')

    capsys.readouterr()

    assert check(repo, "--files", str(source)) == 2

    out = capsys.readouterr().out
    assert "Severity: error" in out
    assert "Instruction:\nAvoid print in library code.\n" in out


def test_diagnostics_group_by_file_then_sort_by_line_and_rule_id(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_check_repo(home)
    first = repo / "a.py"
    # Both rules match line 1; rule-ID order (no-print first) beats column order.
    first.write_text('y = cast(int, print("x"))\nz = cast(str, 2)\n')
    second = repo / "b.py"
    second.write_text("x = cast(int, 1)\n")

    capsys.readouterr()

    assert check(repo, "--files", str(second), str(first)) == 2

    out = capsys.readouterr().out
    locations = re.findall(r"^\S+\.py:\d+:\d+$", out, flags=re.MULTILINE)
    assert locations == ["a.py:1:15", "a.py:1:5", "a.py:2:5", "b.py:1:5"]


def test_renders_at_most_twenty_diagnostics_with_overflow_line(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_check_repo(home)
    source = repo / "src.py"
    source.write_text("".join(f"v{i} = cast(int, {i})\n" for i in range(21)))

    capsys.readouterr()

    assert check(repo, "--files", str(source)) == 2

    out = capsys.readouterr().out
    assert out.startswith("BYOLSP found 21 issues in AI-written code.\n")
    assert out.count("Rule: no-python-cast") == 20
    assert out.endswith(
        "...and 1 more diagnostics. Run ast-grep scan for the full list.\n"
    )


def test_max_results_is_forwarded_to_ast_grep(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_check_repo(home)
    source = repo / "src.py"
    source.write_text("".join(f"v{i} = cast(int, {i})\n" for i in range(3)))

    capsys.readouterr()

    assert check(repo, "--files", str(source), "--max-results", "1") == 2

    out = capsys.readouterr().out
    assert out.startswith("BYOLSP found 1 issue in AI-written code.\n")
    assert "more diagnostics" not in out


def test_json_format_emits_one_issue_per_diagnostic(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_check_repo(home)
    (repo / "src.py").write_text('x = cast(int, "1")\n')

    capsys.readouterr()

    assert check(repo, "--format", "json") == 2  # no --files scans the repo

    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "issues": [
            {
                "file": "src.py",
                "line": 1,
                "column": 5,
                "rule_id": "no-python-cast",
                "severity": "warning",
                "message": "Avoid typing.cast in Python code.",
                "code": 'x = cast(int, "1")',
                "instruction": CAST_PROMPT,
            }
        ]
    }


def test_scan_failure_is_a_clean_tool_error(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_check_repo(home)
    (repo / "sgconfig.yml").write_text("ruleDirs: 5\n")
    (repo / "src.py").write_text("x = 1\n")

    capsys.readouterr()

    assert check(repo, "--files", str(repo / "src.py")) == 1

    captured = capsys.readouterr()
    assert "scan` failed" in captured.err
    assert "Traceback" not in captured.err
