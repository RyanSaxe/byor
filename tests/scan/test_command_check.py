"""Gate agent shell commands: deny with steering, approve silently, fail open.

The pre-command hook runs on every shell command an agent issues, so these tests pin its three
contracts. A rule or check match denies through the harness's JSON permission decision and still
exits 0 — deny is protocol, never an exit code. Repos with nothing to gate approve without spawning
any subprocess, and disabled repos approve silently. Every internal failure — a broken rule blob, a
crashed loader, a check that cannot start or hangs — falls open to allow with a stderr breadcrumb.
`--command` is the human mode: the same deny text on stdout and exit 2 for scripting.
"""

import io
import json
import shlex
import sys
from pathlib import Path

import pytest
from support import make_repo, write_command_rule, write_global_command_rule

from byor.cli import main
from byor.config import CommandCheckDef, load_repo_config, save_repo_config
from byor.scan import checks as checks_module
from byor.scan import command_check
from byor.scan.astgrep import ScanMatch
from byor.scan.checks import CheckOutcome, EffectiveCommandCheck, run_command_checks

PIP_PAYLOAD = {"tool_input": {"command": "cd src && pip install requests"}}


# monkeypatch isolates process state (env, cwd, stdio): an external boundary
# ast-grep-ignore: python.question-mocks
def stdin(monkeypatch: pytest.MonkeyPatch, payload: object) -> None:
    text = json.dumps(payload)
    monkeypatch.setattr(sys, "stdin", io.StringIO(text))


def test_hook_denies_a_matching_command_via_permission_decision(
    home: Path,
    # monkeypatch isolates process state (env, cwd, stdio): an external boundary
    # ast-grep-ignore: python.question-mocks
    monkeypatch: pytest.MonkeyPatch,
    *,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = make_repo(home)
    write_command_rule(repo / ".byor" / "commands" / "project" / "no-pip.yml", "no-pip")
    stdin(monkeypatch, {**PIP_PAYLOAD, "cwd": str(repo)})
    capsys.readouterr()

    assert main(["command-check", "--stdin-hook", "claude-code"]) == 0

    envelope = json.loads(capsys.readouterr().out)["hookSpecificOutput"]
    assert envelope["permissionDecision"] == "deny"
    assert "no-pip" in envelope["permissionDecisionReason"]
    assert "Rewrite the command" in envelope["permissionDecisionReason"]


def test_hook_approves_a_clean_command_with_no_output(
    home: Path,
    # monkeypatch isolates process state (env, cwd, stdio): an external boundary
    # ast-grep-ignore: python.question-mocks
    monkeypatch: pytest.MonkeyPatch,
    *,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = make_repo(home)
    write_command_rule(repo / ".byor" / "commands" / "project" / "no-pip.yml", "no-pip")
    stdin(monkeypatch, {"tool_input": {"command": "ls -la"}, "cwd": str(repo)})
    capsys.readouterr()

    assert main(["command-check", "--stdin-hook", "claude-code"]) == 0

    assert capsys.readouterr().out == ""


def test_a_repo_with_nothing_to_gate_approves_without_any_subprocess(
    home: Path,
    # monkeypatch isolates process state (env, cwd, stdio): an external boundary
    # ast-grep-ignore: python.question-mocks
    monkeypatch: pytest.MonkeyPatch,
    *,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The gate runs on every shell command; its fast path must exit before
    # ast-grep or any check subprocess when there is nothing to apply.
    repo = make_repo(home)
    stdin(monkeypatch, {**PIP_PAYLOAD, "cwd": str(repo)})

    def fail_if_scanned(*_args: object, **_kwargs: object) -> object:
        msg = "scan_command must not run when there are no command rules"
        raise AssertionError(msg)

    monkeypatch.setattr(command_check, "scan_command", fail_if_scanned)
    capsys.readouterr()

    assert main(["command-check", "--stdin-hook", "claude-code"]) == 0

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "internal error" not in captured.err


def test_hook_fails_open_when_the_gate_itself_breaks(
    home: Path,
    # monkeypatch isolates process state (env, cwd, stdio): an external boundary
    # ast-grep-ignore: python.question-mocks
    monkeypatch: pytest.MonkeyPatch,
    *,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = make_repo(home)
    stdin(monkeypatch, {**PIP_PAYLOAD, "cwd": str(repo)})

    def explode(*_args: object, **_kwargs: object) -> object:
        msg = "boom"
        raise RuntimeError(msg)

    monkeypatch.setattr(command_check, "load_effective_command_rules", explode)
    capsys.readouterr()

    assert main(["command-check", "--stdin-hook", "claude-code"]) == 0

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "command-check skipped after an internal error" in captured.err


def test_hook_stays_silent_in_a_disabled_repo(
    home: Path,
    # monkeypatch isolates process state (env, cwd, stdio): an external boundary
    # ast-grep-ignore: python.question-mocks
    monkeypatch: pytest.MonkeyPatch,
    *,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = make_repo(home)
    write_command_rule(repo / ".byor" / "commands" / "project" / "no-pip.yml", "no-pip")
    assert main(["disable", str(repo)]) == 0
    stdin(monkeypatch, {**PIP_PAYLOAD, "cwd": str(repo)})
    capsys.readouterr()

    assert main(["command-check", "--stdin-hook", "claude-code"]) == 0

    assert capsys.readouterr().out == ""


def test_hook_without_a_command_in_the_payload_approves(
    home: Path,
    # monkeypatch isolates process state (env, cwd, stdio): an external boundary
    # ast-grep-ignore: python.question-mocks
    monkeypatch: pytest.MonkeyPatch,
    *,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = make_repo(home)
    write_command_rule(repo / ".byor" / "commands" / "project" / "no-pip.yml", "no-pip")
    stdin(monkeypatch, {"tool_input": {"file_path": "src.py"}, "cwd": str(repo)})
    capsys.readouterr()

    assert main(["command-check", "--stdin-hook", "claude-code"]) == 0

    assert capsys.readouterr().out == ""


def test_an_uninitialized_repo_falls_back_to_global_command_rules(
    home: Path,
    # monkeypatch isolates process state (env, cwd, stdio): an external boundary
    # ast-grep-ignore: python.question-mocks
    monkeypatch: pytest.MonkeyPatch,
    *,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Bootstrap the global config dir via a throwaway initialized repo.
    make_repo(home)
    write_global_command_rule(home, "no-pip.yml", rule_id="no-pip")
    plain = home / "plain"
    plain.mkdir()
    stdin(monkeypatch, {**PIP_PAYLOAD, "cwd": str(plain)})
    capsys.readouterr()

    assert main(["command-check", "--stdin-hook", "claude-code"]) == 0

    reason = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["permissionDecisionReason"]
    assert "no-pip" in reason


def test_command_mode_prints_the_deny_text_and_exits_two(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = make_repo(home)
    write_command_rule(repo / ".byor" / "commands" / "project" / "no-pip.yml", "no-pip")
    capsys.readouterr()

    assert main(["command-check", "--repo", str(repo), "--command", "pip install requests"]) == 2
    out = capsys.readouterr().out
    assert "BYOR blocked this command" in out
    assert "no-pip" in out

    assert main(["command-check", "--repo", str(repo), "--command", "uv add requests"]) == 0
    assert capsys.readouterr().out == ""


def test_a_failing_command_check_script_denies_with_its_output(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = make_repo(home)
    script = repo / "no-pip-check.py"
    script.write_text(
        "import sys\n"
        "command = sys.stdin.read()\n"
        "if 'pip' in command:\n"
        "    print('this repo installs with uv, not pip')\n"
        "    raise SystemExit(1)\n"
    )
    config = load_repo_config(repo)
    config.command_checks = [CommandCheckDef("no-pip-script", shlex.join([sys.executable, str(script)]))]
    save_repo_config(repo, config)
    capsys.readouterr()

    assert main(["command-check", "--repo", str(repo), "--command", "pip install requests"]) == 2
    out = capsys.readouterr().out
    assert "### no-pip-script" in out
    assert "installs with uv" in out

    assert main(["command-check", "--repo", str(repo), "--command", "uv add requests"]) == 0


def test_a_hanging_command_check_degrades_to_a_warning(
    # monkeypatch isolates process state (env, cwd, stdio): an external boundary
    # ast-grep-ignore: python.question-mocks
    monkeypatch: pytest.MonkeyPatch,
    *,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(checks_module, "COMMAND_CHECK_TIMEOUT_SECONDS", 0.2)
    script = tmp_path / "sleeper.py"
    script.write_text("import sys, time\nsys.stdin.read()\ntime.sleep(60)\n")
    check = EffectiveCommandCheck(CommandCheckDef("sleeper", shlex.join([sys.executable, str(script)])), origin="repo")

    outcome = run_command_checks([check], tmp_path, command="ls")

    assert outcome.failures == []
    assert any("timed out" in warning for warning in outcome.warnings)


def test_a_missing_command_check_executable_degrades_to_a_warning(tmp_path: Path) -> None:
    check = EffectiveCommandCheck(CommandCheckDef("ghost", str(tmp_path / "missing-tool")), origin="repo")

    outcome = run_command_checks([check], tmp_path, command="ls")

    assert outcome.failures == []
    assert any("could not run" in warning for warning in outcome.warnings)


def test_render_deny_caps_matches_and_keeps_the_rewrite_instruction() -> None:
    matches = [
        ScanMatch(
            file="STDIN",
            line=1,
            column=1,
            end_line=1,
            rule_id=f"rule-{index}",
            severity="error",
            message="Not here.",
            lines="pip install x",
            agent_prompt="Use uv add.",
        )
        for index in range(3)
    ]

    rendered = command_check.render_deny(matches, CheckOutcome(), limit=1)

    assert "breaks 3 house rules" in rendered
    assert "rule-0" in rendered
    assert "rule-1" not in rendered
    assert "... and 2 more not shown" in rendered
    assert "Do this instead: Use uv add." in rendered
    assert rendered.endswith("Rewrite the command as instructed and run it again.")
