"""Isolated ast-grep subprocess handling (SPEC sections 5, 20).

Every ast-grep invocation lives here: executable resolution, version parsing,
and (for agent-check) JSON scans. No rule-indexing logic. All subprocess
calls pass argv lists, never shell strings (SPEC 19).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from byolsp.errors import AstGrepNotFound, ByolspError

NOT_FOUND_MESSAGE = (
    "ast-grep is required but was not found.\n"
    "\n"
    "Install it, then rerun this command:\n"
    "  brew install ast-grep\n"
    "\n"
    "Other install options:\n"
    "  https://ast-grep.github.io/guide/quick-start.html"
)

VERSION_PATTERN = re.compile(r"\d+(\.\d+)+")


def resolve_ast_grep(command: str = "auto") -> Path:
    """Locate the ast-grep executable (SPEC 5).

    `$BYOLSP_AST_GREP` wins when set. Otherwise a non-`auto` `command` (the
    global config's `ast_grep.command`: a name or absolute path) is used
    exactly, and `auto` tries `ast-grep` then `sg` on PATH.
    """
    override = os.environ.get("BYOLSP_AST_GREP")
    if override:
        candidates: tuple[str, ...] = (override,)
    elif command != "auto":
        candidates = (command,)
    else:
        candidates = ("ast-grep", "sg")
    for candidate in candidates:
        found = shutil.which(candidate)
        if found is not None:
            return Path(found)
    raise AstGrepNotFound(NOT_FOUND_MESSAGE)


@dataclass
class ScanMatch:
    """One `ast-grep scan` match; line and column are 0-based as reported.

    Deliberately raw ast-grep output (SPEC 20 keeps this module isolated):
    rendering transforms live with the consumer, in agent_check.Diagnostic.
    """

    file: str
    line: int
    column: int
    end_line: int
    """range.end.line: the last line the match spans, 0-based as reported."""

    rule_id: str
    severity: str
    message: str
    lines: str
    """The full source line(s) the match spans."""

    agent_prompt: str | None
    """metadata.byolsp.agent_prompt, when the rule carries one."""


@dataclass
class ScanResult:
    matches: list[ScanMatch]
    warnings: str
    """ast-grep's stderr (e.g. an unreadable file); empty when clean."""


def scan_files(
    executable: Path,
    repo_root: Path,
    files: Sequence[Path],
    max_results: int | None = None,
) -> ScanResult:
    """Run `ast-grep scan --json` from repo_root and parse the matches.

    With no `files`, ast-grep scans the whole repository. The exit code is
    ignored when stdout is valid JSON (error-severity matches make ast-grep
    exit nonzero); unparseable output raises ByolspError with ast-grep's
    own message (SPEC 15.9 tool error).
    """
    argv = [
        str(executable),
        "scan",
        "--json=compact",
        "--include-metadata",
        "--color",
        "never",
    ]
    if max_results is not None:
        argv.extend(["--max-results", str(max_results)])
    argv.extend(str(file) for file in files)
    result = subprocess.run(argv, capture_output=True, text=True, cwd=repo_root)
    matches = _parse_scan_output(result.stdout)
    if matches is None:
        detail = result.stderr.strip() or result.stdout.strip()
        message = f"`{executable.name} scan` failed (exit {result.returncode})"
        raise ByolspError(f"{message}:\n{detail}" if detail else message)
    return ScanResult(matches=matches, warnings=result.stderr.strip())


def ast_grep_version(executable: Path) -> str:
    """The version `executable --version` reports, e.g. '0.43.0'."""
    try:
        result = subprocess.run(
            [str(executable), "--version"], capture_output=True, text=True
        )
    except OSError as error:
        raise AstGrepNotFound(
            f"could not run `{executable} --version`: {error}"
        ) from error
    match = VERSION_PATTERN.search(result.stdout) if result.returncode == 0 else None
    if match is None:
        raise AstGrepNotFound(
            f"could not read an ast-grep version from `{executable} --version`"
        )
    return match.group(0)


def _parse_scan_output(stdout: str) -> list[ScanMatch] | None:
    """Parse scan stdout into matches; None when it is not a JSON match list."""
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, list):
        return None
    matches: list[ScanMatch] = []
    for item in payload:
        if not isinstance(item, dict):
            return None
        matches.append(_parse_match(item))
    return matches


def _parse_match(match: dict[str, object]) -> ScanMatch:
    line, column, end_line = _match_positions(match)
    return ScanMatch(
        file=_string_field(match, "file"),
        line=line,
        column=column,
        end_line=end_line,
        rule_id=_string_field(match, "ruleId"),
        severity=_string_field(match, "severity"),
        message=_string_field(match, "message"),
        lines=_string_field(match, "lines"),
        agent_prompt=_agent_prompt(match),
    )


def _string_field(match: dict[str, object], key: str) -> str:
    value = match.get(key)
    if not isinstance(value, str):
        raise ByolspError(f"unexpected ast-grep scan JSON: missing '{key}'")
    return value


def _match_positions(match: dict[str, object]) -> tuple[int, int, int]:
    span = match.get("range")
    start = span.get("start") if isinstance(span, dict) else None
    end = span.get("end") if isinstance(span, dict) else None
    line = start.get("line") if isinstance(start, dict) else None
    column = start.get("column") if isinstance(start, dict) else None
    end_line = end.get("line") if isinstance(end, dict) else None
    if (
        not isinstance(line, int)
        or not isinstance(column, int)
        or not isinstance(end_line, int)
    ):
        raise ByolspError("unexpected ast-grep scan JSON: missing 'range' positions")
    return line, column, end_line


def _agent_prompt(match: dict[str, object]) -> str | None:
    """metadata.byolsp.agent_prompt; lenient because metadata is optional."""
    metadata = match.get("metadata")
    byolsp = metadata.get("byolsp") if isinstance(metadata, dict) else None
    prompt = byolsp.get("agent_prompt") if isinstance(byolsp, dict) else None
    return prompt if isinstance(prompt, str) else None
