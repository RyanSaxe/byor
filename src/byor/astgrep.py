"""Isolated ast-grep subprocess handling.

Every ast-grep invocation lives here: executable resolution, version parsing,
and (for agent-check) JSON scans. No rule-indexing logic. All subprocess
calls pass argv lists, never shell strings.
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

from byor.errors import AstGrepNotFound, ByorError

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
    """Locate the ast-grep executable.

    `$BYOR_AST_GREP` wins when set. Otherwise a non-`auto` `command` (the
    global config's `ast_grep.command`: a name or absolute path) is used
    exactly, and `auto` tries `ast-grep` then `sg` on PATH.

    A candidate is accepted only if `<candidate> --version` reports an
    ast-grep version; otherwise resolution continues down the chain (Ubuntu's
    `/usr/bin/sg` is the unix setgroups tool, not ast-grep). Each resolved path
    is probed at most once per call.
    """
    override = os.environ.get("BYOR_AST_GREP")
    if override:
        candidates: tuple[str, ...] = (override,)
    elif command != "auto":
        candidates = (command,)
    else:
        candidates = ("ast-grep", "sg")
    probed: set[Path] = set()
    for candidate in candidates:
        found = shutil.which(candidate)
        if found is None:
            continue
        executable = Path(found)
        if executable in probed:
            continue
        probed.add(executable)
        if _reports_ast_grep_version(executable):
            return executable
    raise AstGrepNotFound(NOT_FOUND_MESSAGE)


@dataclass
class ScanMatch:
    """One `ast-grep scan` match; line and column are 1-based.

    ast-grep reports 0-based positions; the parse normalizes them once so
    every consumer (linescope ranges, agent_check.Diagnostic) speaks 1-based.
    """

    file: str
    line: int
    column: int
    end_line: int
    """range.end.line: the last line the match spans, 1-based."""

    rule_id: str
    severity: str
    message: str
    lines: str
    """The full source line(s) the match spans."""

    agent_prompt: str | None
    """metadata.byor.agent_prompt, when the rule carries one."""


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
    exit nonzero); unparseable output raises ByorError with ast-grep's
    own message.
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
        raise ByorError(f"{message}:\n{detail}" if detail else message)
    return ScanResult(matches=matches, warnings=result.stderr.strip())


def ast_grep_version(executable: Path) -> str:
    """The version `executable --version` reports, e.g. '0.43.0'."""
    result = _run_version(executable)
    if result is None:
        raise AstGrepNotFound(f"could not run `{executable} --version`")
    version = _parse_ast_grep_version(result)
    if version is None:
        raise AstGrepNotFound(
            f"could not read an ast-grep version from `{executable} --version`"
        )
    return version


def _reports_ast_grep_version(executable: Path) -> bool:
    """Whether `executable --version` succeeds and names an ast-grep version."""
    result = _run_version(executable)
    return result is not None and _parse_ast_grep_version(result) is not None


def _run_version(executable: Path) -> subprocess.CompletedProcess[str] | None:
    """Run `executable --version`, or None when the executable cannot run."""
    try:
        return subprocess.run(
            [str(executable), "--version"], capture_output=True, text=True
        )
    except OSError:
        return None


def _parse_ast_grep_version(result: subprocess.CompletedProcess[str]) -> str | None:
    """The ast-grep version named in a `--version` result, or None.

    ast-grep prints `ast-grep 0.43.0`; a bare version number alone is rejected
    so the unix `sg` (setgroups) tool, which has no such output, falls through.
    """
    if result.returncode != 0:
        return None
    if "ast-grep" not in result.stdout:
        return None
    match = VERSION_PATTERN.search(result.stdout)
    return match.group(0) if match is not None else None


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
        # Strip CR so a CRLF source file does not leak \r into agent feedback.
        lines=_string_field(match, "lines").replace("\r\n", "\n").rstrip("\r"),
        agent_prompt=_agent_prompt(match),
    )


def _string_field(match: dict[str, object], key: str) -> str:
    value = match.get(key)
    if not isinstance(value, str):
        raise ByorError(f"unexpected ast-grep scan JSON: missing '{key}'")
    return value


def _match_positions(match: dict[str, object]) -> tuple[int, int, int]:
    """1-based (line, column, end_line); ast-grep's JSON is 0-based."""
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
        raise ByorError("unexpected ast-grep scan JSON: missing 'range' positions")
    return line + 1, column + 1, end_line + 1


def _agent_prompt(match: dict[str, object]) -> str | None:
    """metadata.byor.agent_prompt; lenient because metadata is optional."""
    metadata = match.get("metadata")
    byor = metadata.get("byor") if isinstance(metadata, dict) else None
    prompt = byor.get("agent_prompt") if isinstance(byor, dict) else None
    return prompt if isinstance(prompt, str) else None
