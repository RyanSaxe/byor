"""Run ast-grep and parse scan output.

Every ast-grep invocation lives here: executable resolution, version parsing, and JSON scan parsing.
Centralizing subprocess behavior keeps rule scanning predictable and lets the rest of BYOR work with
typed scan results.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from byor.errors import AstGrepNotFoundError, ByorError

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = (
    "ScanMatch",
    "ScanResult",
    "ast_grep_version",
    "resolve_ast_grep",
    "scan_files",
)

NOT_FOUND_MESSAGE = (
    "A working ast-grep could not be found.\n"
    "\n"
    "ast-grep normally ships with byor (the ast-grep-cli dependency). If you\n"
    "set $BYOR_AST_GREP or ast_grep.command, point it at a working ast-grep —\n"
    "or install one and put it on PATH:\n"
    "  brew install ast-grep\n"
    "  https://ast-grep.github.io/guide/quick-start.html"
)

VERSION_PATTERN = re.compile(r"\d+(\.\d+)+")


def resolve_ast_grep(command: str = "auto") -> Path:
    """Locate the ast-grep executable.

    `$BYOR_AST_GREP` wins when set. Otherwise a non-`auto` `command` (the
    global config's `ast_grep.command`: a name or absolute path) is used
    exactly, and `auto` tries `ast-grep` then `sg`.

    In `auto` mode each name is looked up on PATH first, then in the directory of
    the running interpreter — where the bundled `ast-grep-cli` console script
    lives. `uv tool install byor` exposes only the `byor` script on PATH, leaving
    that bundled ast-grep invisible to a bare PATH lookup, so this fallback is
    what makes "ast-grep ships with byor" hold for that install.

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
    # The bundled ast-grep sits beside the interpreter; consult it only in auto
    # mode, so an explicit override or configured command is honored exactly.
    fallback_dir = str(Path(sys.executable).parent) if not override and command == "auto" else None
    probed: set[Path] = set()
    for candidate in candidates:
        for found in _candidate_locations(candidate, fallback_dir):
            executable = Path(found)
            if executable in probed:
                continue
            probed.add(executable)
            if _reports_ast_grep_version(executable):
                return executable
    raise AstGrepNotFoundError(NOT_FOUND_MESSAGE)


def _candidate_locations(candidate: str, fallback_dir: str | None) -> list[str]:
    locations: list[str] = []
    on_path = shutil.which(candidate)
    if on_path is not None:
        locations.append(on_path)
    if fallback_dir is not None:
        beside_interpreter = shutil.which(candidate, path=fallback_dir)
        if beside_interpreter is not None:
            locations.append(beside_interpreter)
    return locations


@dataclass
class ScanMatch:
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
    *,
    files: Sequence[Path],
    config: Path | None = None,
) -> ScanResult:
    """Run `ast-grep scan --json` from repo_root and parse the matches.

    With no `files`, ast-grep scans the whole repository. `config` points
    ast-grep at an explicit `sgconfig.yml` instead of the one it would discover
    from `repo_root` — used to apply the global rules in a repo with no config
    of its own. The exit code is ignored when stdout is valid JSON
    (error-severity matches make ast-grep exit nonzero); unparseable output
    raises ByorError with ast-grep's own message.
    """
    argv = [
        str(executable),
        "scan",
        "--json=compact",
        "--include-metadata",
        "--color",
        "never",
    ]
    if config is not None:
        argv.extend(["--config", str(config)])
    argv.extend(str(file) for file in files)
    # ast-grep emits raw UTF-8 JSON; the locale code page would mojibake or
    # crash on Windows, and "replace" keeps a stray bad byte from killing a scan.
    result = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=repo_root,
        check=False,
    )
    matches = _parse_scan_output(result.stdout)
    if matches is None:
        detail = result.stderr.strip() or result.stdout.strip()
        message = f"`{executable.name} scan` failed (exit {result.returncode})"
        raise ByorError(f"{message}:\n{detail}" if detail else message)
    return ScanResult(matches=matches, warnings=result.stderr.strip())


def ast_grep_version(executable: Path) -> str:
    result = _run_version(executable)
    if result is None:
        msg = f"could not run `{executable} --version`"
        raise AstGrepNotFoundError(msg)
    version = _parse_ast_grep_version(result)
    if version is None:
        msg = f"could not read an ast-grep version from `{executable} --version`"
        raise AstGrepNotFoundError(msg)
    return version


def _reports_ast_grep_version(executable: Path) -> bool:
    result = _run_version(executable)
    return result is not None and _parse_ast_grep_version(result) is not None


def _run_version(executable: Path) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            [str(executable), "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError:
        return None


def _parse_ast_grep_version(result: subprocess.CompletedProcess[str]) -> str | None:
    if result.returncode != 0:
        return None
    if "ast-grep" not in result.stdout:
        return None
    match = VERSION_PATTERN.search(result.stdout)
    return match.group(0) if match is not None else None


def _parse_scan_output(stdout: str) -> list[ScanMatch] | None:
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
        msg = f"unexpected ast-grep scan JSON: missing '{key}'"
        raise ByorError(msg)
    return value


def _match_positions(match: dict[str, object]) -> tuple[int, int, int]:
    span = match.get("range")
    start = span.get("start") if isinstance(span, dict) else None
    end = span.get("end") if isinstance(span, dict) else None
    line = start.get("line") if isinstance(start, dict) else None
    column = start.get("column") if isinstance(start, dict) else None
    end_line = end.get("line") if isinstance(end, dict) else None
    if not isinstance(line, int) or not isinstance(column, int) or not isinstance(end_line, int):
        msg = "unexpected ast-grep scan JSON: missing 'range' positions"
        raise ByorError(msg)
    return line + 1, column + 1, end_line + 1


def _agent_prompt(match: dict[str, object]) -> str | None:
    metadata = match.get("metadata")
    byor = metadata.get("byor") if isinstance(metadata, dict) else None
    prompt = byor.get("agent_prompt") if isinstance(byor, dict) else None
    return prompt if isinstance(prompt, str) else None
