#!/usr/bin/env python3
"""Require a short reason on newly added lint or type-check suppressions.

This example check script runs as an agent-only steering check (`gate: false`).
It inspects only lines added relative to `HEAD` (or every line of an untracked
file) so legacy suppressions are never flagged while new suppressions must
carry an inline explanation or a preceding comment.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

SUPPRESSION_RE = re.compile(
    r"(?P<marker>"
    r"#\s*noqa(?::\s*[\w]+(?:\s*,\s*[\w]+)*)?|"
    r"#\s*ty:\s*ignore(?:\[[^\]]+\])?|"
    r"#\s*type:\s*ignore(?:\[[^\]]+\])?|"
    r"oxlint-disable(?:-next-line|-line)?(?:\s+[\w/@]+(?:-[\w/@]+)*(?:\s*,\s*[\w/@]+(?:-[\w/@]+)*)*)?|"
    r"@ts-(?:expect-error|ignore|nocheck)|"
    r"ast-grep-ignore(?::\s*[\w./]+(?:-[\w./]+)*(?:\s*,\s*[\w./]+(?:-[\w./]+)*)*)?"
    r")",
    re.IGNORECASE,
)
COMMENT_PREFIX_RE = re.compile(r"^\s*(?:#|//|/\*|\*)\s*(.+)$")
HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
MIN_REASON_WORDS = 2


def _parse_diff_added_lines(diff_text: str) -> list[tuple[int, str]]:
    added: list[tuple[int, str]] = []
    current_line = 0
    for line in diff_text.splitlines():
        hunk = HUNK_RE.match(line)
        if hunk:
            current_line = int(hunk.group(1))
        elif line.startswith(("+++", "---")):
            continue
        elif line.startswith("+"):
            added.append((current_line, line[1:]))
            current_line += 1
        elif not line.startswith("-"):
            current_line += 1
    return added


def _relative_repo_path(path: Path) -> str:
    resolved = path.resolve()
    cwd = Path.cwd().resolve()
    if resolved.is_relative_to(cwd):
        return resolved.relative_to(cwd).as_posix()
    return str(path)


def _added_lines_for(path: Path, all_lines: list[str]) -> list[tuple[int, str]]:
    git_bin = shutil.which("git")
    if git_bin is None:
        return list(enumerate(all_lines, start=1))

    rel = _relative_repo_path(path)
    tracked = subprocess.run([git_bin, "cat-file", "-e", f"HEAD:{rel}"], capture_output=True, check=False)
    if tracked.returncode != 0:
        return list(enumerate(all_lines, start=1))

    diff = subprocess.run(
        [git_bin, "diff", "HEAD", "-U0", "--", rel],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if diff.returncode != 0:
        return []
    return _parse_diff_added_lines(diff.stdout)


def _has_inline_reason(line: str, match_end: int) -> bool:
    tail = line[match_end:].strip()
    tail = re.sub(r"^(?:--|//|#|:|-|\s)+", "", tail)
    tail = re.sub(r"\*/\s*$", "", tail).strip()
    return len(re.findall(r"[A-Za-z]{2,}", tail)) >= MIN_REASON_WORDS


def _has_preceding_reason(all_lines: list[str], line_no: int) -> bool:
    idx = line_no - 2
    while idx >= 0:
        prev = all_lines[idx]
        if not prev.strip():
            return False
        if SUPPRESSION_RE.search(prev):
            idx -= 1
            continue
        comment = COMMENT_PREFIX_RE.match(prev)
        return bool(comment and len(re.findall(r"[A-Za-z]{2,}", comment.group(1))) >= MIN_REASON_WORDS)
    return False


def _check_file(raw: str) -> list[str]:
    path = Path(raw)
    if not path.is_file():
        return []
    all_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    findings: list[str] = []
    for line_no, line in _added_lines_for(path, all_lines):
        match = SUPPRESSION_RE.search(line)
        if not match:
            continue
        if _has_inline_reason(line, match.end()) or _has_preceding_reason(all_lines, line_no):
            continue
        marker = match.group("marker").strip()
        findings.append(
            f"{raw}:{line_no}: newly added suppression `{marker}` has no explanation. "
            "Fix the underlying issue if possible; if justified, add a short reason inline "
            "(`-- reason` / `// reason`) or on the comment line directly above."
        )
    return findings


def main(argv: list[str]) -> int:
    findings: list[str] = []
    for raw in argv:
        findings.extend(_check_file(raw))
    if findings:
        sys.stdout.write("\n".join(findings) + "\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
