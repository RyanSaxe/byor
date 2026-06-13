"""Line-range scoping for diagnostics: diff hunks and edit locations.

Pure interval logic plus two range sources — uncommitted `git diff HEAD`
hunks and hook-payload edit strings located in the post-edit text. A `None`
result always means "could not scope this file; treat every line as in
scope", which is the fallback chain ending at file scope.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path

from byor.gitio import git_stdout

Range = tuple[int, int]
"""A 1-based inclusive line range."""

_HUNK_HEADER = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", re.MULTILINE)


def diff_ranges(repo_root: Path, file: Path) -> list[Range] | None:
    """Post-image line ranges of `file`'s uncommitted changes.

    Parsed from `git diff HEAD -U0` hunk headers. None when the whole file
    is in scope: git missing, not a git repository, unborn HEAD, or the
    file is untracked (every line is new). An unchanged tracked file
    yields []; pure deletions leave no post-image lines, so they
    contribute nothing.
    """
    tracked = git_stdout(repo_root, "ls-files", "--", str(file))
    if not tracked:
        return None
    diff = git_stdout(repo_root, "diff", "HEAD", "-U0", "--no-color", "--", str(file))
    if diff is None:
        return None
    ranges: list[Range] = []
    for hunk in _HUNK_HEADER.finditer(diff):
        start = int(hunk.group(1))
        count = int(hunk.group(2)) if hunk.group(2) is not None else 1
        if count > 0:
            ranges.append((start, start + count - 1))
    return merge_ranges(ranges)


def edit_ranges(text: str, contents: str | Sequence[str]) -> list[Range] | None:
    """Line ranges where the edit `contents` occur in the post-edit `text`.

    `contents` is one edit string (possibly the whole file) or a list of
    them; both sides are CRLF-normalized before matching. Returns the
    merged union over every occurrence of every edit. None — fall back to
    diff scope — when there are no edits or any edit cannot be located.
    """
    edits = [contents] if isinstance(contents, str) else list(contents)
    if not edits:
        return None
    normalized_text = _normalize_newlines(text)
    ranges: list[Range] = []
    for edit in edits:
        occurrences = _occurrence_ranges(normalized_text, _normalize_newlines(edit))
        if not occurrences:
            return None
        ranges.extend(occurrences)
    return merge_ranges(ranges)


def overlaps(start: int, end: int, ranges: Sequence[Range]) -> bool:
    """Whether the 1-based inclusive [start, end] intersects any range."""
    return any(start <= last and first <= end for first, last in ranges)


def merge_ranges(ranges: Sequence[Range]) -> list[Range]:
    """Sorted union, coalescing overlapping and adjacent ranges."""
    merged: list[Range] = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _occurrence_ranges(text: str, needle: str) -> list[Range]:
    if not needle:
        return []
    ranges: list[Range] = []
    offset = text.find(needle)
    while offset != -1:
        first = text.count("\n", 0, offset) + 1
        # end - 1 keeps an edit's trailing newline on its own last line.
        last = text.count("\n", 0, offset + len(needle) - 1) + 1
        ranges.append((first, last))
        offset = text.find(needle, offset + 1)
    return ranges


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n")
