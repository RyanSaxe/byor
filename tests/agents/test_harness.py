"""Per-harness payload parsers and response emitters.

Every harness wraps an edit in a different JSON envelope: Claude Code tool_input fields, Copilot
stringified tool arguments, Codex apply_patch text. These tests pin both directions — extracting the
edited file and strings from each payload, and emitting diagnostics the way each harness expects
(exit codes, wrapper JSON, Copilot's 10 kB cap with multibyte safety). Malformed payloads must fail
open to an empty result so a broken hook never blocks an edit.
"""

import json
from pathlib import Path

from byor.agents.harness import (
    COPILOT_CONTEXT_CAP,
    HARNESS_CHOICES,
    CommandPayload,
    EditPayload,
    emit,
    emit_deny,
    parse_apply_patch,
    parse_command_payload,
    parse_payload,
)


def test_claude_code_parses_file_and_new_string() -> None:
    raw = json.dumps({"tool_input": {"file_path": "/repo/src.py", "new_string": "x = 1\n"}})

    assert parse_payload("claude-code", raw) == EditPayload(edits={Path("/repo/src.py"): ["x = 1\n"]})


def test_claude_code_collects_multiedit_and_content_strings() -> None:
    raw = json.dumps(
        {
            "tool_input": {
                "file_path": "/repo/src.py",
                "content": "whole file\n",
                "edits": [
                    {"new_string": "a"},
                    {"new_string": "b"},
                    {"old_string": "c"},
                ],
            }
        }
    )

    payload = parse_payload("claude-code", raw)

    assert payload.edits == {Path("/repo/src.py"): ["whole file\n", "a", "b"]}


def test_copilot_decodes_stringified_tool_args_and_edit_text() -> None:
    # Copilot's CLI delivers toolArgs as a JSON-encoded string, not an object.
    raw = json.dumps(
        {
            "toolName": "edit",
            "toolArgs": json.dumps({"path": "/repo/m.go", "new_str": "x = 1\n"}),
        }
    )

    payload = parse_payload("copilot", raw)

    assert payload.edits == {Path("/repo/m.go"): ["x = 1\n"]}


def test_copilot_captures_create_file_text() -> None:
    raw = json.dumps(
        {
            "toolName": "create",
            "toolArgs": json.dumps({"path": "/r/n.py", "file_text": "y\n"}),
        }
    )

    assert parse_payload("copilot", raw) == EditPayload(edits={Path("/r/n.py"): ["y\n"]})


def test_copilot_without_a_recognizable_path_scans_nothing() -> None:
    raw = json.dumps({"toolName": "shell", "toolArgs": json.dumps({"command": "ls"})})

    assert parse_payload("copilot", raw) == EditPayload()


def test_copilot_read_only_tools_scan_nothing() -> None:
    view = {"toolName": "view", "toolArgs": json.dumps({"path": "/r/n.py"})}
    editor_view = {"toolName": "str_replace_editor", "toolArgs": json.dumps({"command": "view", "path": "/r/n.py"})}

    assert parse_payload("copilot", json.dumps(view)) == EditPayload()
    assert parse_payload("copilot", json.dumps(editor_view)) == EditPayload()


def test_codex_parses_the_apply_patch_envelope() -> None:
    patch = "*** Begin Patch\n*** Update File: src/model.py\n@@\n-old = 1\n+new = 1\n+also = 2\n*** End Patch"
    raw = json.dumps({"tool_name": "apply_patch", "tool_input": {"command": patch}})

    payload = parse_payload("codex", raw)

    assert payload.edits == {Path("src/model.py"): ["new = 1\nalso = 2"]}


def test_codex_patch_paths_are_relative_to_the_session_cwd(tmp_path: Path) -> None:
    session_cwd = tmp_path / "repo" / "src"
    patch = "*** Begin Patch\n*** Add File: mod.py\n+print(1)\n*** End Patch"
    raw = json.dumps({"cwd": str(session_cwd), "tool_name": "apply_patch", "tool_input": {"command": patch}})

    assert parse_payload("codex", raw) == EditPayload(edits={session_cwd / "mod.py": ["print(1)"]})


def test_apply_patch_handles_multiple_files_and_pure_deletions() -> None:
    patch = "*** Begin Patch\n*** Add File: a.py\n+first\n+second\n*** Update File: b.py\n-removed\n*** End Patch"

    added = parse_apply_patch(patch)

    assert added == {"a.py": ["first\nsecond"], "b.py": []}


def test_apply_patch_move_to_keys_added_lines_to_the_destination() -> None:
    # The old path no longer exists after the rename applies, so feedback
    # keyed to it would be dropped from the scan entirely.
    rename_with_edit = (
        "*** Begin Patch\n*** Update File: src/old.py\n*** Move to: src/new.py\n@@\n-x = 1\n+print(z)\n*** End Patch"
    )
    pure_rename = "*** Begin Patch\n*** Update File: src/old.py\n*** Move to: src/new.py\n*** End Patch"

    assert parse_apply_patch(rename_with_edit) == {"src/new.py": ["print(z)"]}
    assert parse_apply_patch(pure_rename) == {"src/new.py": []}


def test_codex_rename_payload_scans_the_destination_file() -> None:
    patch = "*** Begin Patch\n*** Update File: old.py\n*** Move to: new.py\n@@\n+print(z)\n*** End Patch"
    raw = json.dumps({"tool_name": "apply_patch", "tool_input": {"command": patch}})

    assert parse_payload("codex", raw) == EditPayload(edits={Path("new.py"): ["print(z)"]})


def test_apply_patch_merges_repeated_update_sections_for_one_file() -> None:
    patch = (
        "*** Begin Patch\n"
        "*** Update File: a.py\n"
        "@@\n"
        "+first_added = 1\n"
        "*** Update File: b.py\n"
        "+b = 2\n"
        "*** Update File: a.py\n"
        "@@\n"
        "+second_added = 3\n"
        "*** End Patch"
    )

    added = parse_apply_patch(patch)

    assert added == {"a.py": ["first_added = 1", "second_added = 3"], "b.py": ["b = 2"]}


def test_malformed_payloads_fail_open_to_an_empty_result() -> None:
    for harness in HARNESS_CHOICES:
        assert parse_payload(harness, "{not json") == EditPayload()
        assert parse_payload(harness, "[]") == EditPayload()
        assert parse_payload(harness, "{}") == EditPayload()


def test_claude_code_emitter_uses_exit_two_and_raw_text() -> None:
    assert emit("claude-code", "diag text") == ("diag text", 2)
    assert emit("claude-code", "") == ("", 0)


def test_codex_emitter_wraps_text_in_hook_specific_output() -> None:
    stdout, code = emit("codex", "diag text")

    assert code == 0
    assert json.loads(stdout) == {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": "diag text",
        }
    }
    assert "\n" not in stdout


def test_copilot_emitter_truncates_to_the_ten_kb_cap() -> None:
    # Newlines double under JSON escaping, so the encoded envelope, not the raw
    # text, must stay within the cap.
    stdout, code = emit("copilot", "line\n" * (COPILOT_CONTEXT_CAP // 2))

    assert code == 0
    assert len(stdout) <= COPILOT_CONTEXT_CAP
    assert json.loads(stdout)["additionalContext"].startswith("line")


def test_copilot_emitter_keeps_multibyte_diagnostics_near_the_cap() -> None:
    # Multibyte characters inflate the JSON-encoded length more than the raw
    # character count; the truncation must measure the encoded envelope, not
    # subtract a byte overshoot from a character slice (which collapsed the text
    # to a fraction of the cap).
    stdout, code = emit("copilot", "diagnostic with em—dashes “quotes” …\n" * 600)

    assert code == 0
    assert len(stdout) <= COPILOT_CONTEXT_CAP
    assert len(stdout) > COPILOT_CONTEXT_CAP * 0.9


def test_empty_diagnostics_emit_plain_exit_zero_everywhere() -> None:
    for harness in HARNESS_CHOICES:
        assert emit(harness, "") == ("", 0)


def test_claude_code_parses_a_pre_command_payload_with_cwd() -> None:
    raw = json.dumps({"tool_input": {"command": "pip install requests"}, "cwd": "/repo"})

    assert parse_command_payload("claude-code", raw) == CommandPayload(
        command="pip install requests", cwd=Path("/repo")
    )


def test_codex_parses_the_pretooluse_payload() -> None:
    # The shape codex 0.144 actually sends to a PreToolUse hook (verified live):
    # tool_name "Bash", tool_input.command string, top-level cwd. Same as Claude
    # Code — the session-rollout log's {cmd, workdir} is a different artifact.
    raw = json.dumps(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "pip install requests"},
            "cwd": "/repo",
        }
    )

    assert parse_command_payload("codex", raw) == CommandPayload(command="pip install requests", cwd=Path("/repo"))


def test_codex_parses_a_string_or_shell_argv_command() -> None:
    as_string = json.dumps({"tool_input": {"command": "pip install requests"}})
    as_shell_argv = json.dumps({"tool_input": {"command": ["bash", "-lc", "pip install requests"]}})
    as_plain_argv = json.dumps({"tool_input": {"command": ["pip", "install", "requests"]}})

    assert parse_command_payload("codex", as_string).command == "pip install requests"
    assert parse_command_payload("codex", as_shell_argv).command == "pip install requests"
    assert parse_command_payload("codex", as_plain_argv).command == "pip install requests"


def test_copilot_parses_the_command_from_stringified_tool_args() -> None:
    raw = json.dumps({"toolArgs": json.dumps({"command": "pip install requests"}), "cwd": "/repo"})

    assert parse_command_payload("copilot", raw) == CommandPayload(command="pip install requests", cwd=Path("/repo"))


def test_malformed_command_payloads_fail_open_to_no_command() -> None:
    for harness in HARNESS_CHOICES:
        assert parse_command_payload(harness, "{not json") == CommandPayload()
        assert parse_command_payload(harness, "{}") == CommandPayload()
    # A different tool's hook firing (no command in tool_input) also approves.
    assert parse_command_payload("claude-code", json.dumps({"tool_input": {"file_path": "x"}})) == CommandPayload()
    assert parse_command_payload("codex", json.dumps({"tool_input": {"command": ["ls", 5]}})) == CommandPayload()
    assert (
        parse_command_payload("antigravity", json.dumps({"toolCall": {"name": "write_file", "args": {"Path": "x"}}}))
        == CommandPayload()
    )


def test_deny_emitters_wrap_the_reason_in_each_harness_envelope() -> None:
    claude_stdout, claude_code = emit_deny("claude-code", "use uv add")
    codex_stdout, codex_code = emit_deny("codex", "use uv add")
    copilot_stdout, copilot_code = emit_deny("copilot", "use uv add")

    assert claude_code == codex_code == copilot_code == 0
    assert json.loads(claude_stdout) == {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": "use uv add",
        }
    }
    assert json.loads(codex_stdout) == json.loads(claude_stdout)
    assert json.loads(copilot_stdout) == {
        "permissionDecision": "deny",
        "permissionDecisionReason": "use uv add",
    }


def test_copilot_deny_reason_respects_the_ten_kb_cap() -> None:
    stdout, code = emit_deny("copilot", "reason\n" * (COPILOT_CONTEXT_CAP // 2))

    assert code == 0
    assert len(stdout) <= COPILOT_CONTEXT_CAP
    assert json.loads(stdout)["permissionDecision"] == "deny"


def test_an_empty_decision_approves_with_no_output_everywhere() -> None:
    for harness in HARNESS_CHOICES:
        assert emit_deny(harness, "") == ("", 0)


def _write_transcript(tmp_path: Path, steps: list[dict[str, object]]) -> Path:
    transcript = tmp_path / "transcript_full.jsonl"
    transcript.write_text("\n".join(json.dumps(step) for step in steps) + "\n", encoding="utf-8")
    return transcript


def test_antigravity_reads_successful_edits_since_initial_num_steps(tmp_path: Path) -> None:
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "step_index": 0,
                "type": "PLANNER_RESPONSE",
                "tool_calls": [{"name": "write_file", "args": {"Path": "/repo/old.py", "Content": "stale = 1\n"}}],
            },
            {"step_index": 1, "type": "GENERIC", "status": "DONE"},
            {
                "step_index": 2,
                "type": "PLANNER_RESPONSE",
                "tool_calls": [
                    {"name": "write_file", "args": {"Path": "/repo/a.py", "Content": "x = 1\n"}},
                    {"name": "edit_file", "args": {"Path": "/repo/b.py", "Target": "old", "Replacement": "new"}},
                    {"name": "edit_file", "args": {"Path": "/repo/failed.py", "Target": "missing", "Replacement": "z"}},
                    {"name": "read_file", "args": {"Path": "/repo/readme.md"}},
                ],
            },
            {"step_index": 3, "type": "GENERIC", "status": "DONE"},
            {"step_index": 4, "type": "GENERIC", "status": "DONE"},
            {"step_index": 5, "type": "GENERIC", "status": "ERROR"},
            {"step_index": 6, "type": "GENERIC", "status": "DONE"},
        ],
    )
    raw = json.dumps({"transcriptPath": str(transcript), "initialNumSteps": 2, "cwd": "/repo"})

    assert parse_payload("antigravity", raw) == EditPayload(
        edits={Path("/repo/a.py"): ["x = 1\n"], Path("/repo/b.py"): ["new"]}
    )


def test_antigravity_resolves_relative_paths_and_handles_ide_tools_and_deletions(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifacts"
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "step_index": 0,
                "type": "PLANNER_RESPONSE",
                "status": "DONE",
                "tool_calls": [
                    {"name": "edit_file", "args": {"Path": "src/del.py", "Target": "x", "Replacement": ""}},
                    {"name": "write_to_file", "args": {"TargetFile": "/repo/w.py", "CodeContent": "w = 1\n"}},
                    {
                        "name": "replace_file_content",
                        "args": {"TargetFile": "/repo/r.py", "ReplacementContent": "r = 2\n"},
                    },
                    {
                        "name": "multi_replace_file_content",
                        "args": {
                            "TargetFile": "/repo/m.py",
                            "ReplacementChunks": [{"ReplacementContent": "a"}, {"ReplacementContent": "b"}],
                        },
                    },
                    {
                        "name": "write_file",
                        "args": {"Path": str(artifact_dir / "plan.md"), "Content": "# Plan\n"},
                    },
                ],
            },
            # An intervening system message must not shift tool-result matching.
            {"step_index": 1, "type": "SYSTEM_MESSAGE", "status": "DONE"},
            {"step_index": 2, "type": "GENERIC", "status": "DONE"},
            {"step_index": 3, "type": "GENERIC", "status": "DONE"},
            {"step_index": 4, "type": "GENERIC", "status": "DONE"},
            {"step_index": 5, "type": "GENERIC", "status": "DONE"},
            {"step_index": 6, "type": "GENERIC", "status": "DONE"},
        ],
    )
    raw = json.dumps(
        {
            "transcriptPath": str(transcript),
            "initialNumSteps": 0,
            "cwd": "/repo",
            "artifactDirectoryPath": str(artifact_dir),
        }
    )

    assert parse_payload("antigravity", raw) == EditPayload(
        edits={
            Path("/repo/src/del.py"): [],
            Path("/repo/w.py"): ["w = 1\n"],
            Path("/repo/r.py"): ["r = 2\n"],
            Path("/repo/m.py"): ["a", "b"],
        }
    )


def test_antigravity_skips_canceled_and_unexecuted_tool_calls(tmp_path: Path) -> None:
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "step_index": 0,
                "type": "PLANNER_RESPONSE",
                "status": "DONE",
                "tool_calls": [
                    {"name": "write_file", "args": {"Path": "/repo/ok.py", "Content": "a = 1\n"}},
                    {"name": "write_file", "args": {"Path": "/repo/canceled.py", "Content": "b = 2\n"}},
                    {"name": "write_file", "args": {"Path": "/repo/aborted.py", "Content": "c = 3\n"}},
                ],
            },
            {"step_index": 1, "type": "GENERIC", "status": "DONE"},
            {"step_index": 2, "type": "GENERIC", "status": "CANCELED"},
        ],
    )
    raw = json.dumps({"transcriptPath": str(transcript), "initialNumSteps": 0})

    assert parse_payload("antigravity", raw) == EditPayload(edits={Path("/repo/ok.py"): ["a = 1\n"]})


def test_antigravity_missing_transcript_fails_open(tmp_path: Path) -> None:
    raw = json.dumps({"transcriptPath": str(tmp_path / "missing.jsonl"), "initialNumSteps": 1})

    assert parse_payload("antigravity", raw) == EditPayload()
    assert parse_payload("antigravity", json.dumps({"transcriptPath": str(tmp_path), "initialNumSteps": 0})) == (
        EditPayload()
    )
    assert parse_payload("antigravity", json.dumps({"transcriptPath": str(tmp_path), "initialNumSteps": True})) == (
        EditPayload()
    )


def test_antigravity_emitter_injects_system_message() -> None:
    stdout, code = emit("antigravity", "diag text")

    assert code == 0
    assert json.loads(stdout) == {"injectSteps": [{"systemMessage": {"systemMessage": "diag text"}}]}
    assert "\n" not in stdout


def test_antigravity_parses_pre_command_payload_and_emits_deny() -> None:
    raw = json.dumps(
        {
            "hookEventName": "PreToolUse",
            "cwd": "/repo",
            "toolCall": {
                "id": "call_1",
                "name": "run_command",
                "args": {"CommandLine": "pip install requests", "WaitMsBeforeAsync": 5000},
            },
        }
    )
    with_arg_cwd = json.dumps(
        {
            "cwd": "/outer",
            "toolCall": {"name": "run_command", "args": {"CommandLine": "pip install x", "Cwd": "/inner"}},
        }
    )
    with_relative_cwd = json.dumps(
        {
            "cwd": "/outer",
            "toolCall": {"name": "run_command", "args": {"CommandLine": "pip install x", "Cwd": "packages/app"}},
        }
    )

    assert parse_command_payload("antigravity", raw) == CommandPayload(
        command="pip install requests", cwd=Path("/repo")
    )
    assert parse_command_payload("antigravity", with_arg_cwd) == CommandPayload(
        command="pip install x", cwd=Path("/inner")
    )
    assert parse_command_payload("antigravity", with_relative_cwd) == CommandPayload(
        command="pip install x", cwd=Path("/outer/packages/app")
    )

    stdout, code = emit_deny("antigravity", "use uv add")
    assert code == 0
    assert json.loads(stdout) == {"decision": "deny", "reason": "use uv add"}
    assert "\n" not in stdout
