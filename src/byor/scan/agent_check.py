"""`byor agent-check`: ast-grep diagnostics rendered for AI agents."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from byor.agents.harness import EditPayload, Harness, emit, parse_payload
from byor.config import load_global_config, repo_config_path
from byor.errors import ByorError
from byor.io.paths import (
    display_path,
    global_config_dir,
    home_sgconfig_path,
    resolve_repo_root,
)
from byor.scan.astgrep import ScanMatch, resolve_ast_grep, scan_files
from byor.scan.checks import CheckOutcome, load_effective_checks, run_checks
from byor.scan.linescope import Range, diff_ranges, edit_ranges, overlaps

DIAGNOSTICS_EXIT_CODE = 2

Scope = Literal["edit", "diff", "file"]


@dataclass
class Diagnostic:
    """One diagnostic ready to render: 1-based position, repo-relative path."""

    file: str
    line: int
    column: int
    rule_id: str
    severity: str
    message: str
    code: str
    instruction: str
    """metadata.byor.agent_prompt, falling back to the rule message."""


def run_agent_check(args: argparse.Namespace) -> int:
    repo_root = resolve_repo_root(explicit=args.repo)
    harness: Harness | None = args.stdin_hook
    if harness is not None:
        return _run_hook(args, repo_root, harness)
    return _run_files(args, repo_root, list(args.files), _resolve_scope(args, None))


def _run_files(
    args: argparse.Namespace, repo_root: Path, files: list[Path], scope: Scope
) -> int:
    """The `--files` path: print diagnostics in the requested text/json format."""
    scoped = _scoped_files(files, scope)
    diagnostics = _diagnostics(args, repo_root, scoped, scope, payload=None)
    checks = load_effective_checks(repo_root, global_config_dir())
    outcome = run_checks(
        checks, repo_root, scoped, whole_repo=scope == "file" and not scoped
    )
    for warning in outcome.warnings:
        print(warning, file=sys.stderr)
    if args.format == "json":
        print(json.dumps(_json_payload(diagnostics, outcome), indent=2))
    else:
        for line in render_diagnostics(diagnostics):
            print(line)
        for section in outcome.failures:
            print(section)
    has_findings = bool(diagnostics) or bool(outcome.failures)
    return DIAGNOSTICS_EXIT_CODE if has_findings else 0


def _run_hook(args: argparse.Namespace, repo_root: Path, harness: Harness) -> int:
    """The `--stdin-hook HARNESS` path: fail-open, never block the agent.

    Any internal byor error is swallowed to a silent exit 0 so a global-scope
    hook (which carries no shell `|| true` guard) cannot block the agent loop
    on a byor bug or config problem.
    """
    # fail-open: a byor bug must never block the agent loop
    try:  # ast-grep-ignore: no-broad-except
        return _hook_diagnostics(args, repo_root, harness)
    except Exception:
        return 0


def _hook_diagnostics(
    args: argparse.Namespace, repo_root: Path, harness: Harness
) -> int:
    """Parse the payload, scan, and emit per harness.

    Global hooks fire in every repo. A byor-init'd repo scopes against its own
    rules; any other repo applies your global rules via the home sgconfig and any
    user-owned global checks. With none of those in play there is nothing to
    check, so byor stays silent (exit 0) without reading stdin or shelling out.
    """
    config_dir = global_config_dir()
    if not _has_any_rules(repo_root) and not load_global_config(config_dir).checks:
        return 0
    payload = _resolved_payload(parse_payload(harness, sys.stdin.read()), repo_root)
    if not payload.files:
        return 0
    scope = _resolve_scope(args, harness)
    scoped = _scoped_files(payload.files, scope)
    diagnostics = _diagnostics(args, repo_root, scoped, scope, payload)
    checks = load_effective_checks(repo_root, config_dir)
    outcome = run_checks(checks, repo_root, scoped)
    for warning in outcome.warnings:
        print(warning, file=sys.stderr)
    rendered = _render_feedback(diagnostics, outcome)
    stdout, exit_code = emit(harness, rendered)
    if stdout:
        print(stdout)
    return exit_code


def _scoped_files(raw_files: list[Path], scope: Scope) -> list[Path]:
    """Resolve targets, dropping deleted ones under edit/diff scope.

    An edit/diff-scoped target deleted since the edit has no lines left to
    scope; under file scope an empty `--files` means the whole repository, so
    the empty list is preserved.
    """
    files = [file.resolve() for file in raw_files]
    if scope == "file" or not files:
        return files
    return [file for file in files if file.is_file()]


def _json_payload(
    diagnostics: list[Diagnostic], outcome: CheckOutcome
) -> dict[str, list[dict[str, object]] | list[str]]:
    payload: dict[str, list[dict[str, object]] | list[str]] = {
        "issues": [asdict(diagnostic) for diagnostic in diagnostics]
    }
    if outcome.failures:
        payload["checks"] = outcome.failures
    return payload


def _render_feedback(diagnostics: list[Diagnostic], outcome: CheckOutcome) -> str:
    """Combine ast-grep diagnostics and failing-check sections for the emitter."""
    sections = render_diagnostics(diagnostics) + outcome.failures
    return "\n".join(sections)


def _has_any_rules(repo_root: Path) -> bool:
    """Whether a scan could surface anything: repo rules or a global setup.

    The cheap pre-check that keeps a global hook a near-instant no-op in a repo
    that is neither byor-init'd nor covered by `~/sgconfig.yml`.
    """
    return repo_config_path(repo_root).is_file() or home_sgconfig_path().is_file()


def _diagnostics(
    args: argparse.Namespace,
    repo_root: Path,
    files: list[Path],
    scope: Scope,
    payload: EditPayload | None,
) -> list[Diagnostic]:
    if scope != "file" and not files:
        return []
    if not _has_any_rules(repo_root):
        return []
    config = None if repo_config_path(repo_root).is_file() else home_sgconfig_path()
    config_dir = global_config_dir()
    executable = resolve_ast_grep(load_global_config(config_dir).ast_grep_command)
    result = scan_files(executable, repo_root, files, config=config)
    if result.warnings:
        print(result.warnings, file=sys.stderr)
    matches = result.matches
    if scope != "file":
        matches = _matches_in_scope(matches, repo_root, scope, payload)
    return collect_diagnostics(matches, repo_root)


def _resolve_scope(args: argparse.Namespace, harness: Harness | None) -> Scope:
    """The diagnostic scope: explicit flag wins, else per mode.

    Hook mode defaults to edit (payload contents locate the lines); `--files`
    defaults to file. The fallback chain edit -> diff -> file is applied later,
    per file, when contents cannot be located.
    """
    scope: str | None = args.scope
    if scope == "edit":
        if harness is None:
            raise ByorError(
                "--scope edit needs a hook payload; use --stdin-hook, or --scope diff"
            )
        return "edit"
    if scope == "diff":
        return "diff"
    if scope == "file":
        return "file"
    return "edit" if harness is not None else "file"


def _matches_in_scope(
    matches: list[ScanMatch],
    repo_root: Path,
    scope: Scope,
    payload: EditPayload | None,
) -> list[ScanMatch]:
    """Matches overlapping their file's in-scope line ranges.

    A `None` range means the file could not be scoped (untracked, non-git,
    unborn HEAD, or — under edit scope — edit contents that could not be
    located in the post-edit text); every match is kept, the fallback to file
    scope.
    """
    ranges_by_file: dict[str, list[Range] | None] = {}
    in_scope = []
    for match in matches:
        if match.file not in ranges_by_file:
            file = (repo_root / match.file).resolve()
            ranges_by_file[match.file] = _file_ranges(repo_root, file, scope, payload)
        ranges = ranges_by_file[match.file]
        if ranges is None or overlaps(match.line, match.end_line, ranges):
            in_scope.append(match)
    return in_scope


def _file_ranges(
    repo_root: Path, file: Path, scope: Scope, payload: EditPayload | None
) -> list[Range] | None:
    """In-scope line ranges for one file under the active scope.

    Edit scope locates the payload's edit strings in the post-edit text and
    falls back to diff scope when they cannot be found (or the harness gave no
    contents); diff scope's own None fallback then reaches file scope.
    """
    if scope == "edit" and payload is not None:
        ranges = _edit_ranges_for(file, payload)
        if ranges is not None:
            return ranges
    return diff_ranges(repo_root, file)


def _edit_ranges_for(file: Path, payload: EditPayload) -> list[Range] | None:
    contents = payload.edits.get(file, [])
    if not contents:
        return None
    return edit_ranges(file.read_text(encoding="utf-8"), contents)


def _resolved_payload(payload: EditPayload, repo_root: Path) -> EditPayload:
    """Resolve the payload's paths so they match the scanned files' keys.

    A relative path from the harness is taken against the repo root, where the
    agent runs, not the process cwd, so it lines up with the scanned files.
    """

    def resolve(path: Path) -> Path:
        return path.resolve() if path.is_absolute() else (repo_root / path).resolve()

    return EditPayload(
        files=[resolve(file) for file in payload.files],
        edits={resolve(file): contents for file, contents in payload.edits.items()},
    )


def collect_diagnostics(matches: list[ScanMatch], repo_root: Path) -> list[Diagnostic]:
    """1-based diagnostics grouped by file, sorted by line then rule ID."""
    diagnostics = [
        Diagnostic(
            file=display_path(Path(match.file), repo_root),
            line=match.line,
            column=match.column,
            rule_id=match.rule_id,
            severity=match.severity,
            message=match.message,
            code=match.lines.rstrip("\n"),
            instruction=(match.agent_prompt or match.message).strip(),
        )
        for match in matches
    ]
    diagnostics.sort(key=lambda d: (d.file, d.line, d.rule_id, d.column))
    return diagnostics


def render_diagnostics(diagnostics: list[Diagnostic]) -> list[str]:
    """The rendered text output; empty when there are no diagnostics."""
    if not diagnostics:
        return []
    total = len(diagnostics)
    noun = "issue" if total == 1 else "issues"
    lines = [f"BYOR found {total} {noun} in AI-written code."]
    for diagnostic in diagnostics:
        lines += [
            "",
            f"{diagnostic.file}:{diagnostic.line}:{diagnostic.column}",
            f"Rule: {diagnostic.rule_id}",
            f"Severity: {diagnostic.severity}",
            f"Message: {diagnostic.message}",
            *_render_code(diagnostic.code, diagnostic.line),
            "",
            "Instruction:",
            diagnostic.instruction,
        ]
    return lines


def _render_code(code: str, start_line: int) -> list[str]:
    """Render exact source indentation behind a clearly separate line gutter."""
    source_lines = code.splitlines()
    width = len(str(start_line + len(source_lines) - 1))
    numbered = [
        f"  {line_number:>{width}} | {line}"
        for line_number, line in enumerate(source_lines, start=start_line)
    ]
    return ["Code:", *numbered]
