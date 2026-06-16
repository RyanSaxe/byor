"""Per-harness payload parsers and response emitters."""

import json
from pathlib import Path

from byor.agents.harness import (
    COPILOT_CONTEXT_CAP,
    EditPayload,
    emit,
    parse_apply_patch,
    parse_payload,
)


def test_claude_code_parses_file_and_new_string() -> None:
    raw = json.dumps(
        {"tool_input": {"file_path": "/repo/src.py", "new_string": "x = 1\n"}}
    )

    assert parse_payload("claude-code", raw) == EditPayload(
        files=[Path("/repo/src.py")], edits={Path("/repo/src.py"): ["x = 1\n"]}
    )


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


def test_cursor_parses_file_path_and_edits() -> None:
    raw = json.dumps(
        {
            "file_path": "/repo/app.ts",
            "edits": [{"old_string": "old", "new_string": "new"}],
        }
    )

    assert parse_payload("cursor", raw) == EditPayload(
        files=[Path("/repo/app.ts")], edits={Path("/repo/app.ts"): ["new"]}
    )


def test_copilot_decodes_stringified_tool_args_and_edit_text() -> None:
    # Copilot's CLI delivers toolArgs as a JSON-encoded string, not an object.
    raw = json.dumps(
        {
            "toolName": "edit",
            "toolArgs": json.dumps({"path": "/repo/m.go", "new_str": "x = 1\n"}),
        }
    )

    payload = parse_payload("copilot", raw)

    assert payload.files == [Path("/repo/m.go")]
    assert payload.edits == {Path("/repo/m.go"): ["x = 1\n"]}


def test_copilot_captures_create_file_text() -> None:
    raw = json.dumps(
        {
            "toolName": "create",
            "toolArgs": json.dumps({"path": "/r/n.py", "file_text": "y\n"}),
        }
    )

    assert parse_payload("copilot", raw) == EditPayload(
        files=[Path("/r/n.py")], edits={Path("/r/n.py"): ["y\n"]}
    )


def test_copilot_without_a_recognizable_path_scans_nothing() -> None:
    raw = json.dumps({"toolName": "shell", "toolArgs": json.dumps({"command": "ls"})})

    assert parse_payload("copilot", raw) == EditPayload()


def test_codex_parses_the_apply_patch_envelope() -> None:
    patch = (
        "*** Begin Patch\n"
        "*** Update File: src/model.py\n"
        "@@\n"
        "-old = 1\n"
        "+new = 1\n"
        "+also = 2\n"
        "*** End Patch"
    )
    raw = json.dumps({"tool_name": "shell", "tool_input": {"command": patch}})

    payload = parse_payload("codex", raw)

    assert payload.files == [Path("src/model.py")]
    assert payload.edits == {Path("src/model.py"): ["new = 1\nalso = 2"]}


def test_apply_patch_handles_multiple_files_and_pure_deletions() -> None:
    patch = (
        "*** Begin Patch\n"
        "*** Add File: a.py\n"
        "+first\n"
        "+second\n"
        "*** Update File: b.py\n"
        "-removed\n"
        "*** End Patch"
    )

    added = parse_apply_patch(patch)

    assert added == {"a.py": ["first\nsecond"], "b.py": []}


def test_malformed_payloads_fail_open_to_an_empty_result() -> None:
    for harness in ("claude-code", "codex", "copilot", "cursor"):
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


def test_cursor_emitter_uses_additional_context() -> None:
    stdout, code = emit("cursor", "diag text")

    assert code == 0
    assert json.loads(stdout) == {"additional_context": "diag text"}


def test_copilot_emitter_truncates_to_the_ten_kb_cap() -> None:
    # Newlines double under JSON escaping, so the encoded envelope, not the raw
    # text, must stay within the cap.
    stdout, code = emit("copilot", "line\n" * (COPILOT_CONTEXT_CAP // 2))

    assert code == 0
    assert len(stdout) <= COPILOT_CONTEXT_CAP
    assert json.loads(stdout)["additionalContext"].startswith("line")


def test_empty_diagnostics_emit_plain_exit_zero_everywhere() -> None:
    for harness in ("claude-code", "codex", "copilot", "cursor"):
        assert emit(harness, "") == ("", 0)
