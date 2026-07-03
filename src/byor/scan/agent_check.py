"""Render BYOR diagnostics for AI agents.

Agent checks combine ast-grep matches, custom check failures, edit or diff scoping, and harness-
specific output. This module owns the high-level scan flow so hooks can fail open while command-line
scans remain strict.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

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

if TYPE_CHECKING:
    import argparse

    from byor.config import GlobalConfig

__all__ = (
    "Diagnostic",
    "collect_diagnostics",
    "render_diagnostics",
    "run_agent_check",
)

DIAGNOSTICS_EXIT_CODE = 2
# A deliberate catch-all: the hook path fails open because a crashed check must
# never block an agent's edit, so any exception a scan raises is swallowed
# (with a stderr breadcrumb) rather than enumerated.
FAIL_OPEN_ERRORS = (Exception,)

Scope = Literal["edit", "diff", "file"]
DiagnosticStyle = Literal["verbose", "concise"]
Audience = Literal["agent", "human"]
"""Who reads the findings: a harness hook feeds an agent; --files faces a human."""


@dataclass
class Diagnostic:
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
    harness: Harness | None = args.stdin_hook
    if harness is not None:
        return _run_hook(args, harness=harness)
    return _run_files(
        args,
        resolve_repo_root(explicit=args.repo),
        files=list(args.files),
        scope=_resolve_scope(args, None),
    )


def _run_files(
    args: argparse.Namespace,
    repo_root: Path,
    *,
    files: list[Path],
    scope: Scope,
) -> int:
    config_dir = global_config_dir()
    global_config = load_global_config(config_dir)
    diagnostics, outcome = _scan(
        repo_root,
        files,
        scope=scope,
        payload=None,
        global_config=global_config,
        config_dir=config_dir,
    )
    if args.format == "json":
        sys.stdout.write(f"{json.dumps(_json_payload(diagnostics, outcome), indent=2)}\n")
    else:
        concise = args.concise or global_config.output_concise
        for line in render_diagnostics(
            diagnostics,
            style="concise" if concise else "verbose",
            limit=global_config.output_max_diagnostics,
            audience="human",
        ):
            sys.stdout.write(f"{line}\n")
        for section in outcome.failures:
            sys.stdout.write(f"{section}\n")
    has_findings = bool(diagnostics) or bool(outcome.failures)
    return DIAGNOSTICS_EXIT_CODE if has_findings else 0


def _run_hook(args: argparse.Namespace, *, harness: Harness) -> int:
    """Run the fail-open `--stdin-hook HARNESS` path.

    Any internal byor error is caught and reported on stderr but still exits 0,
    so a global-scope hook (which carries no shell `|| true` guard) cannot block
    the agent loop on a byor bug or config problem.
    """
    # fail-open: a byor bug must never block the agent loop
    try:
        return _hook_diagnostics(args, harness=harness)
    except FAIL_OPEN_ERRORS as error:
        # Still exit 0 (never block), but leave a breadcrumb so "byor couldn't
        # run" is distinguishable from "byor found nothing". Hook stderr is not
        # fed to the agent on exit 0, so this stays out of its context; `byor
        # doctor` surfaces the root cause (e.g. a missing rule directory).
        sys.stderr.write(f"byor: agent-check skipped after an internal error: {error}\n")
        return 0


def _hook_diagnostics(args: argparse.Namespace, *, harness: Harness) -> int:
    """Parse the payload, scan, and emit per harness.

    Global hooks fire in every repo, and an agent session can edit a file
    outside its cwd's repository, so the repo is resolved from the payload's
    edited file rather than the cwd. A byor-init'd repo scopes against its own
    rules; any other repo applies your global rules via the home sgconfig and
    any user-owned global checks. With none of those in play there is nothing
    to check, so byor stays silent (exit 0) without shelling out.
    """
    config_dir = global_config_dir()
    global_config = load_global_config(config_dir)
    payload = parse_payload(harness, sys.stdin.read())
    if not payload.edits:
        return 0
    repo_root = _hook_repo_root(explicit=args.repo, payload=payload)
    if not _has_any_rules(repo_root) and not global_config.checks:
        return 0
    payload = _resolved_payload(payload, repo_root)
    diagnostics, outcome = _scan(
        repo_root,
        list(payload.edits),
        scope=_resolve_scope(args, harness),
        payload=payload,
        global_config=global_config,
        config_dir=config_dir,
    )
    concise = args.concise or global_config.output_concise
    rendered = _render_feedback(
        diagnostics,
        outcome,
        concise=concise,
        limit=global_config.output_max_diagnostics,
    )
    stdout, exit_code = emit(harness, rendered)
    if stdout:
        sys.stdout.write(f"{stdout}\n")
    return exit_code


def _scan(
    repo_root: Path,
    files: list[Path],
    *,
    scope: Scope,
    payload: EditPayload | None,
    global_config: GlobalConfig,
    config_dir: Path,
) -> tuple[list[Diagnostic], CheckOutcome]:
    # The shared middle of the `--files` and `--stdin-hook` flows; only how
    # the results are rendered and exited on differs per caller.
    scoped = _scoped_files(files, scope)
    diagnostics = _diagnostics(repo_root, scoped, scope=scope, payload=payload, global_config=global_config)
    checks = load_effective_checks(repo_root, config_dir)
    outcome = run_checks(
        checks,
        repo_root,
        files=scoped,
        whole_repo=scope == "file" and not scoped,
    )
    for warning in outcome.warnings:
        sys.stderr.write(f"{warning}\n")
    return diagnostics, outcome


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


def _render_feedback(
    diagnostics: list[Diagnostic],
    outcome: CheckOutcome,
    *,
    concise: bool,
    limit: int | None,
) -> str:
    sections = (
        render_diagnostics(
            diagnostics,
            style="concise" if concise else "verbose",
            limit=limit,
            audience="agent",
        )
        + outcome.failures
    )
    return "\n".join(sections)


def _has_any_rules(repo_root: Path) -> bool:
    return repo_config_path(repo_root).is_file() or home_sgconfig_path().is_file()


def _diagnostics(
    repo_root: Path,
    files: list[Path],
    *,
    scope: Scope,
    payload: EditPayload | None,
    global_config: GlobalConfig,
) -> list[Diagnostic]:
    if scope != "file" and not files:
        return []
    if not _has_any_rules(repo_root):
        return []
    config = None if repo_config_path(repo_root).is_file() else home_sgconfig_path()
    executable = resolve_ast_grep(global_config.ast_grep_command)
    result = scan_files(executable, repo_root, files=files, config=config)
    if result.warnings:
        sys.stderr.write(f"{result.warnings}\n")
    matches = result.matches
    if scope != "file":
        matches = _matches_in_scope(matches, repo_root, scope=scope, payload=payload)
    return collect_diagnostics(matches, repo_root)


def _resolve_scope(args: argparse.Namespace, harness: Harness | None) -> Scope:
    """Resolve the diagnostic scope from explicit flags or mode.

    Hook mode defaults to edit (payload contents locate the lines); `--files`
    defaults to file. The fallback chain edit -> diff -> file is applied later,
    per file, when contents cannot be located.
    """
    scope: str | None = args.scope
    if scope == "edit":
        if harness is None:
            msg = "--scope edit needs a hook payload; use --stdin-hook, or --scope diff"
            raise ByorError(msg)
        return "edit"
    if scope == "diff":
        return "diff"
    if scope == "file":
        return "file"
    return "edit" if harness is not None else "file"


def _matches_in_scope(
    matches: list[ScanMatch],
    repo_root: Path,
    *,
    scope: Scope,
    payload: EditPayload | None,
) -> list[ScanMatch]:
    """Filter matches to their file's in-scope line ranges.

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
            ranges_by_file[match.file] = _file_ranges(
                repo_root,
                file,
                scope=scope,
                payload=payload,
            )
        ranges = ranges_by_file[match.file]
        if ranges is None or overlaps(match.line, match.end_line, ranges=ranges):
            in_scope.append(match)
    return in_scope


def _file_ranges(
    repo_root: Path,
    file: Path,
    *,
    scope: Scope,
    payload: EditPayload | None,
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


def _hook_repo_root(*, explicit: Path | None, payload: EditPayload) -> Path:
    """Resolve the repo whose rules govern the edited file.

    A hook fires with the session's cwd, which need not contain the file the
    agent edited; starting from the first absolute edited path applies that
    file's own repo rules and keeps the cwd repo's out. Relative paths (codex
    patches) fall back to cwd resolution, since only the invoking repo can
    anchor them. An explicit --repo still wins inside resolve_repo_root.
    """
    start = next((file.parent for file in payload.edits if file.is_absolute()), None)
    return resolve_repo_root(explicit=explicit, start=start)


def _resolved_payload(payload: EditPayload, repo_root: Path) -> EditPayload:
    def resolve(path: Path) -> Path:
        return path.resolve() if path.is_absolute() else (repo_root / path).resolve()

    return EditPayload(edits={resolve(file): contents for file, contents in payload.edits.items()})


def collect_diagnostics(matches: list[ScanMatch], repo_root: Path) -> list[Diagnostic]:
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


def render_diagnostics(
    diagnostics: list[Diagnostic],
    *,
    style: DiagnosticStyle = "verbose",
    limit: int | None = None,
    audience: Audience,
) -> list[str]:
    if not diagnostics:
        return []
    header = [_summary_line(diagnostics, audience=audience)]
    shown = diagnostics if limit is None else diagnostics[:limit]
    body = _render_concise(shown) if style == "concise" else _render_verbose(shown)
    hidden = len(diagnostics) - len(shown)
    if hidden:
        body += ["", f"... and {hidden} more not shown (raise output.max_diagnostics)."]
    return header + body


def _summary_line(diagnostics: list[Diagnostic], *, audience: Audience) -> str:
    total = len(diagnostics)
    noun = "issue" if total == 1 else "issues"
    # Agents key on the hook-mode wording exactly; a human running --files
    # (e.g. from the pre-commit gate) did not just write AI code.
    suffix = " in AI-written code" if audience == "agent" else ""
    return f"BYOR found {total} {noun}{suffix}."


def _render_verbose(diagnostics: list[Diagnostic]) -> list[str]:
    lines: list[str] = []
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


def _render_concise(diagnostics: list[Diagnostic]) -> list[str]:
    lines: list[str] = []
    for diagnostic in diagnostics:
        location = f"{diagnostic.file}:{diagnostic.line}:{diagnostic.column}"
        lines += [
            "",
            f"{location}  [{diagnostic.severity}] {diagnostic.rule_id}",
            diagnostic.instruction,
        ]
    return lines


def _render_code(code: str, start_line: int) -> list[str]:
    source_lines = code.splitlines()
    width = len(str(start_line + len(source_lines) - 1))
    numbered = [f"  {line_number:>{width}} | {line}" for line_number, line in enumerate(source_lines, start=start_line)]
    return ["Code:", *numbered]
