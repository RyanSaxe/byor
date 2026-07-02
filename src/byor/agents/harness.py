"""Translate AI harness hook payloads into BYOR edits.

Each supported agent reports edited files differently, so this module normalizes those payloads and
formats feedback for the harness. Keeping those adapters together makes hook behavior testable
without coupling scanning logic to one agent.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, TypeAlias

__all__ = (
    "EditPayload",
    "emit",
    "parse_apply_patch",
    "parse_payload",
)

JsonValue: TypeAlias = "None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]"

Harness = Literal["claude-code", "codex", "copilot"]

HARNESS_CHOICES: tuple[Harness, ...] = ("claude-code", "codex", "copilot")

# Copilot caps additionalContext at 10KB; keep a margin for the JSON envelope.
COPILOT_CONTEXT_CAP = 10_000


@dataclass
class EditPayload:
    """One harness payload normalized: edited files and their edit text.

    `edits[path]` holds the literal post-edit strings the harness reported for
    that file, used for edit-scope line ranges. A file mapped to no edit
    strings means "scope this file by diff" — the fallback when contents
    cannot be located.
    """

    edits: dict[Path, list[str]] = field(default_factory=dict)


def parse_payload(harness: Harness, raw: str) -> EditPayload:
    payload = _load_object(raw)
    if payload is None:
        return EditPayload()
    return _PARSERS[harness](payload)


def emit(harness: Harness, rendered: str) -> tuple[str, int]:
    """Return the harness's stdout and exit code for rendered diagnostics.

    `rendered` is empty when there are no diagnostics. Only claude-code uses a
    nonzero exit (2, its stderr-feedback contract); the others always exit 0
    and carry diagnostics in a JSON envelope on stdout.
    """
    if not rendered:
        return "", 0
    return _EMITTERS[harness](rendered)


def _parse_claude_code(payload: dict[str, JsonValue]) -> EditPayload:
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return EditPayload()
    file_path = _string(tool_input.get("file_path"))
    if file_path is None:
        return EditPayload()
    return EditPayload(edits={Path(file_path): _claude_edit_contents(tool_input)})


def _claude_edit_contents(tool_input: dict[str, JsonValue]) -> list[str]:
    direct = _strings(tool_input, ("new_string", "content"))
    return direct + _new_strings_from_edits(tool_input.get("edits"))


def _parse_copilot(payload: dict[str, JsonValue]) -> EditPayload:
    tool_args = _copilot_tool_args(payload.get("toolArgs"))
    if tool_args is None:
        return EditPayload()
    file_path = next(iter(_strings(tool_args, ("path", "filePath", "file_path"))), None)
    if file_path is None:
        return EditPayload()
    contents = _strings(tool_args, ("new_str", "file_text", "new_string", "content"))
    return EditPayload(edits={Path(file_path): contents})


def _copilot_tool_args(value: JsonValue) -> dict[str, JsonValue] | None:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return None
    return value if isinstance(value, dict) else None


def _parse_codex(payload: dict[str, JsonValue]) -> EditPayload:
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return EditPayload()
    command = _string(tool_input.get("command"))
    if command is None:
        return EditPayload()
    added = parse_apply_patch(command)
    return EditPayload(edits={Path(raw_path): contents for raw_path, contents in added.items()})


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
            _flush_patch_section(added, current, plus_lines=plus_lines)
            current, plus_lines = target, []
            added.setdefault(target, [])
            continue
        if current is not None and line.startswith("+"):
            plus_lines.append(line[1:])
    _flush_patch_section(added, current, plus_lines=plus_lines)
    return added


def _patch_file_header(line: str) -> str | None:
    for marker in ("*** Add File: ", "*** Update File: "):
        if line.startswith(marker):
            return line[len(marker) :].strip()
    return None


def _flush_patch_section(added: dict[str, list[str]], current: str | None, *, plus_lines: list[str]) -> None:
    if current is not None and plus_lines:
        added[current] = ["\n".join(plus_lines)]


def _emit_claude_code(rendered: str) -> tuple[str, int]:
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


def _truncate_to_cap(rendered: str) -> str:
    """Keep the longest prefix whose encoded copilot envelope fits the 10KB cap.

    JSON-escaping inflates the encoded length unevenly (a newline doubles, a
    multibyte character grows several-fold), so the budget is on the *encoded*
    envelope, not the character count. A binary search measures in encoded
    length but slices in characters; the old subtract-the-overshoot loop mixed
    the two and collapsed multibyte text to almost nothing.
    """
    if _fits_cap(rendered):
        return rendered
    low, high = 0, len(rendered)
    while low < high:
        mid = (low + high + 1) // 2
        if _fits_cap(rendered[:mid]):
            low = mid
        else:
            high = mid - 1
    return rendered[:low]


def _fits_cap(text: str) -> bool:
    encoded = json.dumps({"additionalContext": text}, separators=(",", ":"))
    return len(encoded) <= COPILOT_CONTEXT_CAP


def _load_object(raw: str) -> dict[str, JsonValue] | None:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _string(value: JsonValue) -> str | None:
    return value if isinstance(value, str) and value else None


def _strings(mapping: dict[str, JsonValue], keys: tuple[str, ...]) -> list[str]:
    return [value for key in keys if (value := _string(mapping.get(key))) is not None]


def _new_strings_from_edits(edits: JsonValue) -> list[str]:
    if not isinstance(edits, list):
        return []
    return [
        value for edit in edits if isinstance(edit, dict) and (value := _string(edit.get("new_string"))) is not None
    ]


_Parser = Callable[[dict[str, JsonValue]], EditPayload]
_Emitter = Callable[[str], tuple[str, int]]

_PARSERS: dict[Harness, _Parser] = {
    "claude-code": _parse_claude_code,
    "codex": _parse_codex,
    "copilot": _parse_copilot,
}

_EMITTERS: dict[Harness, _Emitter] = {
    "claude-code": _emit_claude_code,
    "codex": _emit_codex,
    "copilot": _emit_copilot,
}
