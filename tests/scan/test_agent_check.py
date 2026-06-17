"""`byor agent-check` against the real ast-grep binary."""

import io
import json
import re
import shlex
import sys
from pathlib import Path

import pytest
from support import commit_file, git, make_repo, write_global_rule

from byor.cli import main
from byor.config import (
    CheckDef,
    load_global_config,
    load_repo_config,
    save_global_config,
    save_repo_config,
)
from byor.scaffold.sgconfig import ensure_home_sgconfig
from byor.scan.agent_check import Diagnostic, render_diagnostics

CAST_PROMPT = (
    "Do not use typing.cast here. Fix the type by narrowing, changing the "
    "signature, introducing a protocol, or restructuring the value flow."
)

# A worked example, with agent_prompt folded to one line.
CAST_RULE = """\
id: no-python-cast
language: Python
severity: warning
message: Avoid typing.cast in Python code.
rule:
  pattern: cast($TYPE, $VALUE)
metadata:
  byor:
    agent_prompt: >
      Do not use typing.cast here. Fix the type by narrowing, changing the
      signature, introducing a protocol, or restructuring the value flow.
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

# A call that spans several source lines, so its match range covers more than
# one line — used to test multi-line overlap under edit scope.
FOO_RULE = """\
id: no-foo
language: Python
severity: warning
message: Do not call foo.
rule:
  pattern: foo($$$ARGS)
"""


@pytest.fixture
def check_repo(home: Path, capsys: pytest.CaptureFixture[str]) -> Path:
    """An initialized repo with both rules, init output already flushed."""
    repo = make_repo(home)
    project = repo / ".byor" / "rules" / "project"
    (project / "no-python-cast.yml").write_text(CAST_RULE)
    (project / "no-print.yml").write_text(PRINT_RULE)
    capsys.readouterr()
    return repo


def agent_check_args(repo: Path, *extra: str) -> list[str]:
    return ["agent-check", "--repo", str(repo), *extra]


def add_repo_check(repo: Path, name: str, extensions: list[str], run: str) -> None:
    config = load_repo_config(repo)
    config.checks.append(CheckDef(name, extensions, run))
    save_repo_config(repo, config)


def add_global_check(home: Path, name: str, extensions: list[str], run: str) -> None:
    config_dir = home / "xdg" / "byor"
    config = load_global_config(config_dir)
    config.checks.append(CheckDef(name, extensions, run))
    save_global_config(config_dir, config)


def failing_check_command(repo: Path, message: str) -> str:
    """A check command that prints `message` and exits nonzero."""
    script = repo / "fail_check.py"
    script.write_text(f"import sys\nprint({message!r})\nsys.exit(1)\n")
    return shlex.join([sys.executable, str(script)])


def test_clean_files_exit_zero_with_no_output(
    check_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = check_repo / "src.py"
    source.write_text("x = 1\n")

    assert main(agent_check_args(check_repo, "--files", str(source))) == 0

    assert capsys.readouterr().out == ""


def test_one_diagnostic_renders_the_spec_block(
    check_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = check_repo / "src.py"
    source.write_text('from typing import cast\nx = cast(int, "1")\n')

    assert main(agent_check_args(check_repo, "--files", str(source))) == 2

    assert capsys.readouterr().out == (
        "BYOR found 1 issue in AI-written code.\n"
        "\n"
        "src.py:2:5\n"
        "Rule: no-python-cast\n"
        "Severity: warning\n"
        "Message: Avoid typing.cast in Python code.\n"
        "Code:\n"
        '  2 | x = cast(int, "1")\n'
        "\n"
        "Instruction:\n"
        f"{CAST_PROMPT}\n"
    )


def test_concise_render_keeps_location_severity_and_instruction() -> None:
    diagnostic = Diagnostic(
        file="src.py",
        line=2,
        column=5,
        rule_id="no-print",
        severity="warning",
        message="Avoid print in library code.",
        code='print("x")',
        instruction="Use the logger instead.",
    )

    assert render_diagnostics([diagnostic], concise=True) == [
        "BYOR found 1 issue in AI-written code.",
        "",
        "src.py:2:5  [warning] no-print",
        "Use the logger instead.",
    ]


def test_concise_flag_trims_the_verbose_block(
    check_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = check_repo / "src.py"
    source.write_text('from typing import cast\nx = cast(int, "1")\n')

    args = agent_check_args(check_repo, "--files", str(source), "--concise")
    assert main(args) == 2

    assert capsys.readouterr().out == (
        "BYOR found 1 issue in AI-written code.\n"
        "\n"
        "src.py:2:5  [warning] no-python-cast\n"
        f"{CAST_PROMPT}\n"
    )


def test_global_concise_setting_applies_without_the_flag(
    check_repo: Path, home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config_dir = home / "xdg" / "byor"
    config = load_global_config(config_dir)
    config.output_concise = True
    save_global_config(config_dir, config)
    source = check_repo / "src.py"
    source.write_text('from typing import cast\nx = cast(int, "1")\n')

    assert main(agent_check_args(check_repo, "--files", str(source))) == 2

    concise = capsys.readouterr().out
    assert "src.py:2:5  [warning] no-python-cast" in concise
    assert "Code:" not in concise


def test_multiline_code_renders_exact_indentation_behind_numbered_gutter(
    check_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = check_repo / "src.py"
    source.write_text(
        "\n" * 7 + "def call_foo() -> None:\n"
        "    foo(\n"
        '        "first",\n'
        '        "second",\n'
        "    )\n"
    )
    project = check_repo / ".byor" / "rules" / "project"
    (project / "no-foo.yml").write_text(FOO_RULE)

    assert main(agent_check_args(check_repo, "--files", str(source))) == 2

    assert (
        "Code:\n"
        "   9 |     foo(\n"
        '  10 |         "first",\n'
        '  11 |         "second",\n'
        "  12 |     )\n"
    ) in capsys.readouterr().out


def test_instruction_falls_back_to_message(
    check_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = check_repo / "src.py"
    source.write_text('print("hi")\n')

    assert main(agent_check_args(check_repo, "--files", str(source))) == 2

    out = capsys.readouterr().out
    assert "Severity: error" in out
    assert "Instruction:\nAvoid print in library code.\n" in out


def test_diagnostics_group_by_file_then_sort_by_line_and_rule_id(
    check_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    first = check_repo / "a.py"
    # Both rules match line 1; rule-ID order (no-print first) beats column order.
    first.write_text('y = cast(int, print("x"))\nz = cast(str, 2)\n')
    second = check_repo / "b.py"
    second.write_text("x = cast(int, 1)\n")

    assert main(agent_check_args(check_repo, "--files", str(second), str(first))) == 2

    out = capsys.readouterr().out
    locations = re.findall(r"^\S+\.py:\d+:\d+$", out, flags=re.MULTILINE)
    assert locations == ["a.py:1:15", "a.py:1:5", "a.py:2:5", "b.py:1:5"]


def test_renders_every_diagnostic_without_truncation(
    check_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Every in-scope diagnostic is rendered: the agent must see the full set,
    not a truncated sample it could mistake for the whole job."""
    source = check_repo / "src.py"
    source.write_text("".join(f"v{i} = cast(int, {i})\n" for i in range(21)))

    assert main(agent_check_args(check_repo, "--files", str(source))) == 2

    out = capsys.readouterr().out
    assert out.startswith("BYOR found 21 issues in AI-written code.\n")
    assert out.count("Rule: no-python-cast") == 21
    assert "more diagnostics" not in out


def test_json_format_emits_one_issue_per_diagnostic(
    check_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (check_repo / "src.py").write_text('x = cast(int, "1")\n')

    # No --files scans the repo.
    assert main(agent_check_args(check_repo, "--format", "json")) == 2

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


def stdin(monkeypatch: pytest.MonkeyPatch, payload: object) -> None:
    text = json.dumps(payload)
    monkeypatch.setattr(sys, "stdin", io.StringIO(text))


def test_stdin_hook_scans_the_edited_file_from_the_claude_payload(
    check_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = check_repo / "src.py"
    source.write_text('x = cast(int, "1")\n')
    stdin(monkeypatch, {"tool_input": {"file_path": str(source)}})

    assert main(agent_check_args(check_repo, "--stdin-hook", "claude-code")) == 2

    assert "Rule: no-python-cast" in capsys.readouterr().out


def test_stdin_hook_without_a_file_path_scans_nothing(
    check_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (check_repo / "src.py").write_text('x = cast(int, "1")\n')
    stdin(monkeypatch, {"tool_input": {}})

    assert main(agent_check_args(check_repo, "--stdin-hook", "claude-code")) == 0

    assert capsys.readouterr().out == ""


def test_edit_scope_keeps_only_violations_inside_the_edited_lines(
    check_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = check_repo / "src.py"
    source.write_text("a = cast(int, 1)\nb = cast(int, 2)\nc = cast(int, 3)\n")
    # The payload's new_string names only line 2 as the edit.
    stdin(
        monkeypatch,
        {"tool_input": {"file_path": str(source), "new_string": "b = cast(int, 2)"}},
    )

    assert main(agent_check_args(check_repo, "--stdin-hook", "claude-code")) == 2

    out = capsys.readouterr().out
    assert out.count("Rule: no-python-cast") == 1
    assert "src.py:2:5" in out


def test_codex_hook_edit_scopes_an_apply_patch_and_emits_its_json(
    check_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = check_repo / "src.py"
    source.write_text("a = cast(int, 1)\nb = cast(int, 2)\n")
    patch = (
        "*** Begin Patch\n"
        f"*** Update File: {source}\n"
        "@@\n"
        "+b = cast(int, 2)\n"
        "*** End Patch"
    )
    stdin(monkeypatch, {"tool_name": "apply_patch", "tool_input": {"command": patch}})

    assert main(agent_check_args(check_repo, "--stdin-hook", "codex")) == 0

    payload = json.loads(capsys.readouterr().out)
    context = payload["hookSpecificOutput"]["additionalContext"]
    assert context.count("Rule: no-python-cast") == 1
    assert "src.py:2:5" in context


def test_codex_relative_patch_path_resolves_against_the_repo_root(
    check_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (check_repo / "src.py").write_text("a = cast(int, 1)\n")
    patch = (
        "*** Begin Patch\n"
        "*** Update File: src.py\n"  # relative, as codex reports it
        "@@\n"
        "+a = cast(int, 1)\n"
        "*** End Patch"
    )
    stdin(monkeypatch, {"tool_input": {"command": patch}})

    assert main(agent_check_args(check_repo, "--stdin-hook", "codex")) == 0

    context = json.loads(capsys.readouterr().out)["hookSpecificOutput"][
        "additionalContext"
    ]
    assert "src.py:1:5" in context


def test_hook_mode_is_silent_in_an_uninitialized_repo(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    bare = home / "bare"
    bare.mkdir()
    source = bare / "src.py"
    source.write_text('x = cast(int, "1")\n')
    stdin(monkeypatch, {"tool_input": {"file_path": str(source)}})

    assert main(agent_check_args(bare, "--stdin-hook", "claude-code")) == 0

    assert capsys.readouterr().out == ""


def setup_global_rule(home: Path, rule_id: str = "no-python-cast") -> None:
    """Install a global rule and the home sgconfig that exposes it everywhere."""
    write_global_rule(home, f"{rule_id}.yml", rule_id)
    ensure_home_sgconfig(home / "xdg" / "byor" / "rules", home=home)


def test_files_mode_applies_global_rules_in_a_repo_with_no_byor(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    setup_global_rule(home)
    plain = home / "plain"
    plain.mkdir()
    source = plain / "src.py"
    source.write_text('x = cast(int, "1")\n')
    capsys.readouterr()

    assert main(["agent-check", "--repo", str(plain), "--files", str(source)]) == 2

    assert "Rule: no-python-cast" in capsys.readouterr().out


def test_files_mode_is_silent_without_repo_or_global_rules(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    plain = home / "plain"
    plain.mkdir()
    source = plain / "src.py"
    source.write_text('x = cast(int, "1")\n')
    capsys.readouterr()

    assert main(["agent-check", "--repo", str(plain), "--files", str(source)]) == 0

    assert capsys.readouterr().out == ""


def test_hook_mode_applies_global_rules_in_a_repo_with_no_byor(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    setup_global_rule(home)
    plain = home / "plain"
    plain.mkdir()
    source = plain / "src.py"
    source.write_text('x = cast(int, "1")\n')
    stdin(monkeypatch, {"tool_input": {"file_path": str(source)}})
    capsys.readouterr()

    assert main(agent_check_args(plain, "--stdin-hook", "claude-code")) == 2

    assert "Rule: no-python-cast" in capsys.readouterr().out


def test_failing_check_appends_a_named_section_and_exits_two(
    check_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    add_repo_check(
        check_repo, "lint", ["py"], failing_check_command(check_repo, "bad style")
    )
    source = check_repo / "src.py"
    source.write_text("x = 1\n")  # clean for ast-grep, so only the check fails

    assert main(agent_check_args(check_repo, "--files", str(source))) == 2

    out = capsys.readouterr().out
    assert "### lint" in out
    assert "bad style" in out


def test_failing_check_rides_the_harness_emitter_channel(
    check_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    add_repo_check(
        check_repo, "lint", ["py"], failing_check_command(check_repo, "bad style")
    )
    source = check_repo / "src.py"
    source.write_text("x = 1\n")  # clean for ast-grep, so only the check fails
    patch = f"*** Begin Patch\n*** Update File: {source}\n@@\n+x = 1\n*** End Patch"
    stdin(monkeypatch, {"tool_input": {"command": patch}})

    assert main(agent_check_args(check_repo, "--stdin-hook", "codex")) == 0

    context = json.loads(capsys.readouterr().out)["hookSpecificOutput"][
        "additionalContext"
    ]
    assert "### lint" in context
    assert "bad style" in context


def test_check_does_not_run_for_files_outside_its_extensions(
    check_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    add_repo_check(
        check_repo, "js-lint", ["js"], failing_check_command(check_repo, "js problem")
    )
    source = check_repo / "src.py"
    source.write_text("x = 1\n")

    assert main(agent_check_args(check_repo, "--files", str(source))) == 0

    assert capsys.readouterr().out == ""


def test_files_mode_runs_global_checks_in_a_repo_with_no_byor(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    plain = home / "plain"
    plain.mkdir()
    add_global_check(home, "lint", ["py"], failing_check_command(plain, "global check"))
    source = plain / "src.py"
    source.write_text("x = 1\n")
    capsys.readouterr()

    assert main(["agent-check", "--repo", str(plain), "--files", str(source)]) == 2

    assert "global check" in capsys.readouterr().out


def test_hook_mode_runs_global_checks_in_a_repo_with_no_byor(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plain = home / "plain"
    plain.mkdir()
    add_global_check(home, "lint", ["py"], failing_check_command(plain, "global check"))
    source = plain / "src.py"
    source.write_text("x = 1\n")
    stdin(monkeypatch, {"tool_input": {"file_path": str(source)}})
    capsys.readouterr()

    assert main(agent_check_args(plain, "--stdin-hook", "claude-code")) == 2

    assert "global check" in capsys.readouterr().out


def test_repo_checks_do_not_leak_into_other_repos(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo_a = make_repo(home, "a")
    add_repo_check(
        repo_a, "lint", ["py"], failing_check_command(repo_a, "repo-a check")
    )
    repo_b = make_repo(home, "b")
    source = repo_b / "src.py"
    source.write_text("x = 1\n")
    capsys.readouterr()

    assert main(["agent-check", "--repo", str(repo_b), "--files", str(source)]) == 0

    assert "repo-a check" not in capsys.readouterr().out


def test_whole_repo_mode_runs_checks_without_files(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_repo(home)
    add_repo_check(repo, "lint", ["py"], failing_check_command(repo, "whole repo"))
    capsys.readouterr()

    # No --files means scan the whole repository, checks included.
    assert main(["agent-check", "--repo", str(repo)]) == 2

    assert "whole repo" in capsys.readouterr().out


def test_missing_check_command_warns_and_keeps_diagnostics(
    check_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    add_repo_check(check_repo, "ghost", ["py"], "byor-no-such-command --x")
    source = check_repo / "src.py"
    source.write_text('x = cast(int, "1")\n')

    assert main(agent_check_args(check_repo, "--files", str(source))) == 2

    captured = capsys.readouterr()
    assert "Rule: no-python-cast" in captured.out
    assert "ghost" in captured.err
    assert "### ghost" not in captured.out


def commit_violation(repo: Path) -> Path:
    git(repo, "init", "--quiet")
    return commit_file(repo, "src.py", "a = cast(int, 1)\n")


def test_diff_scope_silences_committed_violations_file_scope_reports(
    check_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = commit_violation(check_repo)

    assert (
        main(agent_check_args(check_repo, "--files", str(source), "--scope", "diff"))
        == 0
    )
    assert capsys.readouterr().out == ""

    assert (
        main(agent_check_args(check_repo, "--files", str(source), "--scope", "file"))
        == 2
    )
    assert "Rule: no-python-cast" in capsys.readouterr().out


def test_diff_scope_keeps_uncommitted_lines_and_untracked_files(
    check_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = commit_violation(check_repo)
    source.write_text("a = cast(int, 1)\nb = cast(int, 2)\n")
    untracked = check_repo / "new.py"
    untracked.write_text("c = cast(int, 3)\n")

    files = ("--files", str(source), str(untracked))
    assert main(agent_check_args(check_repo, *files, "--scope", "diff")) == 2

    out = capsys.readouterr().out
    locations = re.findall(r"^\S+\.py:\d+:\d+$", out, flags=re.MULTILINE)
    assert locations == ["new.py:1:5", "src.py:2:5"]


def test_edit_scope_falls_back_to_diff_when_the_edit_cannot_be_located(
    check_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An edit payload whose text is not in the file drops to diff scope: a
    committed violation stays silent while an uncommitted one is reported."""
    git(check_repo, "init", "--quiet")
    source = commit_file(check_repo, "src.py", "a = cast(int, 1)\n")
    source.write_text("a = cast(int, 1)\nb = cast(int, 2)\n")  # line 2 uncommitted
    stdin(
        monkeypatch,
        {"tool_input": {"file_path": str(source), "new_string": "absent text"}},
    )

    assert main(agent_check_args(check_repo, "--stdin-hook", "claude-code")) == 2

    out = capsys.readouterr().out
    assert out.count("Rule: no-python-cast") == 1  # only the uncommitted line 2
    assert "src.py:2:5" in out


def test_edit_scope_falls_all_the_way_to_file_scope_without_git(
    check_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Edit unlocatable and no git history: diff scope is None too, so the
    chain reaches file scope and every match is reported."""
    source = check_repo / "src.py"
    source.write_text("a = cast(int, 1)\nb = cast(int, 2)\n")
    stdin(
        monkeypatch,
        {"tool_input": {"file_path": str(source), "new_string": "absent text"}},
    )

    assert main(agent_check_args(check_repo, "--stdin-hook", "claude-code")) == 2

    assert capsys.readouterr().out.count("Rule: no-python-cast") == 2


def test_edit_scope_keeps_a_multiline_match_touched_on_an_inner_line(
    check_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A diagnostic spanning several lines is kept when the edit touches any of
    them, not only its first line."""
    (check_repo / ".byor" / "rules" / "project" / "no-foo.yml").write_text(FOO_RULE)
    source = check_repo / "src.py"
    source.write_text("result = foo(\n    1,\n    2,\n)\n")  # foo(...) spans lines 1-4
    # The payload names only line 2 of the four-line call.
    stdin(
        monkeypatch,
        {"tool_input": {"file_path": str(source), "new_string": "    1,"}},
    )

    assert main(agent_check_args(check_repo, "--stdin-hook", "claude-code")) == 2

    assert "Rule: no-foo" in capsys.readouterr().out


def test_diff_scope_in_a_non_git_repo_degrades_to_file_scope(
    check_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """No git history means no diff to scope against, so diff scope reports
    every match rather than erroring."""
    source = check_repo / "src.py"
    source.write_text('x = cast(int, "1")\n')

    assert (
        main(agent_check_args(check_repo, "--files", str(source), "--scope", "diff"))
        == 2
    )

    assert "Rule: no-python-cast" in capsys.readouterr().out


def test_edit_scope_without_a_hook_payload_is_a_clean_error(
    check_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = check_repo / "src.py"
    source.write_text("a = cast(int, 1)\n")

    assert (
        main(agent_check_args(check_repo, "--files", str(source), "--scope", "edit"))
        == 1
    )

    captured = capsys.readouterr()
    assert "--scope edit needs a hook payload" in captured.err
    assert "Traceback" not in captured.err


def test_missing_files_are_dropped_silently_under_diff_scope(
    check_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = check_repo / "gone.py"

    assert (
        main(agent_check_args(check_repo, "--files", str(missing), "--scope", "diff"))
        == 0
    )

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_scan_failure_is_a_clean_tool_error(
    check_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (check_repo / "sgconfig.yml").write_text("ruleDirs: 5\n")
    (check_repo / "src.py").write_text("x = 1\n")

    assert (
        main(agent_check_args(check_repo, "--files", str(check_repo / "src.py"))) == 1
    )

    captured = capsys.readouterr()
    assert "scan` failed" in captured.err
    assert "Traceback" not in captured.err


def test_hook_mode_fails_open_on_an_internal_error(
    check_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The same broken sgconfig that errors under --files must never block the
    # agent: a global-scope hook has no shell `|| true`, so byor exits 0 itself.
    (check_repo / "sgconfig.yml").write_text("ruleDirs: 5\n")
    source = check_repo / "src.py"
    source.write_text('x = cast(int, "1")\n')
    stdin(monkeypatch, {"tool_input": {"file_path": str(source)}})

    assert main(agent_check_args(check_repo, "--stdin-hook", "claude-code")) == 0

    captured = capsys.readouterr()
    assert captured.out == ""
    # Exit 0 never blocks, but a stderr breadcrumb keeps the failure visible.
    assert "skipped after an internal error" in captured.err
