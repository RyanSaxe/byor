"""Normalized hook payloads and responses across the five harnesses.

Each harness pipes a different post-edit JSON shape on stdin and reads back a
different feedback channel. This module is the single normalized pipeline:
`parse_payload` turns any harness's stdin JSON into an `EditPayload` (the
touched files plus, when locatable, the literal edited text), and `emit`
renders byor's diagnostics into the harness's response format. Every parser
fails open — an unrecognized or malformed payload yields an empty
`EditPayload` so the agent loop is never blocked.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, TypeAlias

JsonValue: TypeAlias = (
    "None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]"
)

Harness = Literal["claude-code", "codex", "copilot", "cursor"]

HARNESS_CHOICES: tuple[Harness, ...] = ("claude-code", "codex", "copilot", "cursor")

# Copilot caps additionalContext at 10KB; keep a margin for the JSON envelope.
COPILOT_CONTEXT_CAP = 10_000


@dataclass
class EditPayload:
    """One harness payload normalized: edited files and their edit text.

    `edits[path]` holds the literal post-edit strings the harness reported for
    that file, used for edit-scope line ranges. A file present with no edit
    strings (or absent edits entirely) means "scope this file by diff" — the
    Fallback when contents cannot be located.
    """

    files: list[Path] = field(default_factory=list)
    edits: dict[Path, list[str]] = field(default_factory=dict)


def parse_payload(harness: Harness, raw: str) -> EditPayload:
    """Normalize a harness's stdin JSON; an unparseable payload scans nothing."""
    payload = _load_object(raw)
    if payload is None:
        return EditPayload()
    return _PARSERS[harness](payload)


def emit(harness: Harness, rendered: str) -> tuple[str, int]:
    """The harness's (stdout, exit code) for rendered diagnostics.

    `rendered` is empty when there are no diagnostics. Only claude-code uses a
    nonzero exit (2, its stderr-feedback contract); the others always exit 0
    and carry diagnostics in a JSON envelope on stdout.
    """
    if not rendered:
        return "", 0
    return _EMITTERS[harness](rendered)


def _parse_claude_code(payload: dict[str, JsonValue]) -> EditPayload:
    """tool_input.file_path with old_string/new_string/edits[]/content edits."""
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return EditPayload()
    file_path = _string(tool_input.get("file_path"))
    if file_path is None:
        return EditPayload()
    contents = _claude_edit_contents(tool_input)
    path = Path(file_path)
    return EditPayload(files=[path], edits={path: contents})


def _claude_edit_contents(tool_input: dict[str, JsonValue]) -> list[str]:
    """Post-edit strings from a Write/Edit/MultiEdit payload, in any combination."""
    direct = _strings(tool_input, ("new_string", "content"))
    return direct + _new_strings_from_edits(tool_input.get("edits"))


def _parse_cursor(payload: dict[str, JsonValue]) -> EditPayload:
    """file_path plus edits[] of old_string/new_string pairs."""
    file_path = _string(payload.get("file_path"))
    if file_path is None:
        return EditPayload()
    contents = _new_strings_from_edits(payload.get("edits"))
    path = Path(file_path)
    return EditPayload(files=[path], edits={path: contents})


def _parse_copilot(payload: dict[str, JsonValue]) -> EditPayload:
    """toolArgs (a JSON-encoded string) carrying the edit tool's path and text."""
    tool_args = _copilot_tool_args(payload.get("toolArgs"))
    if tool_args is None:
        return EditPayload()
    file_path = next(iter(_strings(tool_args, ("path", "filePath", "file_path"))), None)
    if file_path is None:
        return EditPayload()
    contents = _strings(tool_args, ("new_str", "file_text", "new_string", "content"))
    path = Path(file_path)
    return EditPayload(files=[path], edits={path: contents})


def _copilot_tool_args(value: JsonValue) -> dict[str, JsonValue] | None:
    """Copilot delivers `toolArgs` as a JSON string; decode it to a mapping.

    The SDK surface documents an object instead, so an already-decoded mapping is
    accepted too; anything else (or undecodable JSON) scans nothing.
    """
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return None
    return value if isinstance(value, dict) else None


def _parse_codex(payload: dict[str, JsonValue]) -> EditPayload:
    """tool_input.command carrying an apply_patch envelope to parse."""
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return EditPayload()
    command = _string(tool_input.get("command"))
    if command is None:
        return EditPayload()
    return _payload_from_patch(parse_apply_patch(command))


def parse_apply_patch(text: str) -> dict[str, list[str]]:
    """Added-line contents per target file in an apply_patch envelope.

    Reads the `*** Add File:` / `*** Update File:` sections of the patch and
    collects each section's added (`+`) lines, joined into one edit string per
    file. Files with no added lines (pure deletions) map to an empty list, so
    the caller still scopes them by diff.
    """
    added: dict[str, list[str]] = {}
    current: str | None = None
    plus_lines: list[str] = []
    for line in text.splitlines():
        target = _patch_file_header(line)
        if target is not None:
            _flush_patch_section(added, current, plus_lines)
            current, plus_lines = target, []
            added.setdefault(target, [])
            continue
        if current is not None and line.startswith("+"):
            plus_lines.append(line[1:])
    _flush_patch_section(added, current, plus_lines)
    return added


def _patch_file_header(line: str) -> str | None:
    for marker in ("*** Add File: ", "*** Update File: "):
        if line.startswith(marker):
            return line[len(marker) :].strip()
    return None


def _flush_patch_section(
    added: dict[str, list[str]], current: str | None, plus_lines: list[str]
) -> None:
    if current is not None and plus_lines:
        added[current] = ["\n".join(plus_lines)]


def _payload_from_patch(added_by_file: dict[str, list[str]]) -> EditPayload:
    payload = EditPayload()
    for raw_path, contents in added_by_file.items():
        path = Path(raw_path)
        payload.files.append(path)
        payload.edits[path] = contents
    return payload


def _emit_claude_code(rendered: str) -> tuple[str, int]:
    """Diagnostics go to stderr via the hook command's redirect; exit 2 here."""
    return rendered, 2


def _emit_codex(rendered: str) -> tuple[str, int]:
    # Codex's PostToolUse output schema requires hookEventName alongside
    # additionalContext, unlike the flat-envelope harnesses below.
    envelope: dict[str, JsonValue] = {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": rendered,
        }
    }
    return json.dumps(envelope, separators=(",", ":")), 0


def _emit_copilot(rendered: str) -> tuple[str, int]:
    envelope: dict[str, JsonValue] = {"additionalContext": _truncate_to_cap(rendered)}
    return json.dumps(envelope, separators=(",", ":")), 0


def _emit_cursor(rendered: str) -> tuple[str, int]:
    envelope: dict[str, JsonValue] = {"additional_context": rendered}
    return json.dumps(envelope, separators=(",", ":")), 0


def _truncate_to_cap(rendered: str) -> str:
    """Trim diagnostics so the encoded copilot envelope fits the 10KB cap.

    JSON-escaping a newline doubles its byte count, so the encoded length is
    bounded by trimming the raw text until the envelope fits rather than by a
    fixed character budget.
    """
    text = rendered
    while text:
        encoded = json.dumps({"additionalContext": text}, separators=(",", ":"))
        overshoot = len(encoded) - COPILOT_CONTEXT_CAP
        if overshoot <= 0:
            break
        text = text[: -max(overshoot, 1)]
    return text


def _load_object(raw: str) -> dict[str, JsonValue] | None:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _string(value: JsonValue) -> str | None:
    return value if isinstance(value, str) and value else None


def _strings(mapping: dict[str, JsonValue], keys: tuple[str, ...]) -> list[str]:
    """Every non-empty string value across `keys`, in key order."""
    return [value for key in keys if (value := _string(mapping.get(key))) is not None]


def _new_strings_from_edits(edits: JsonValue) -> list[str]:
    """The `new_string` of each dict in an `edits[]` list; empty otherwise."""
    if not isinstance(edits, list):
        return []
    return [
        value
        for edit in edits
        if isinstance(edit, dict)
        and (value := _string(edit.get("new_string"))) is not None
    ]


_Parser = Callable[[dict[str, JsonValue]], EditPayload]
_Emitter = Callable[[str], tuple[str, int]]

_PARSERS: dict[Harness, _Parser] = {
    "claude-code": _parse_claude_code,
    "codex": _parse_codex,
    "copilot": _parse_copilot,
    "cursor": _parse_cursor,
}

_EMITTERS: dict[Harness, _Emitter] = {
    "claude-code": _emit_claude_code,
    "codex": _emit_codex,
    "copilot": _emit_copilot,
    "cursor": _emit_cursor,
}
