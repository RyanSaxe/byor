"""Line-range scoping: edit location, diff hunks, and overlap (SPEC 28.3)."""

import subprocess
from pathlib import Path

from byolsp.linescope import diff_ranges, edit_ranges, merge_ranges, overlaps

TEXT = "alpha\nbravo\ncharlie\ndelta\necho\n"


def test_edit_ranges_locates_a_single_edit_string() -> None:
    assert edit_ranges(TEXT, "bravo\ncharlie") == [(2, 3)]


def test_edit_ranges_unions_a_list_of_edits() -> None:
    assert edit_ranges(TEXT, ["alpha", "delta"]) == [(1, 1), (4, 4)]
    assert edit_ranges(TEXT, ["alpha\nbravo", "bravo\ncharlie"]) == [(1, 3)]


def test_edit_ranges_covers_every_occurrence() -> None:
    assert edit_ranges("x = 1\ny = 2\nx = 1\n", "x = 1") == [(1, 1), (3, 3)]


def test_edit_ranges_spans_whole_content() -> None:
    assert edit_ranges(TEXT, TEXT) == [(1, 5)]


def test_edit_ranges_matches_across_crlf_differences() -> None:
    assert edit_ranges(TEXT.replace("\n", "\r\n"), "bravo\ncharlie") == [(2, 3)]
    assert edit_ranges(TEXT, "bravo\r\ncharlie") == [(2, 3)]


def test_edit_ranges_returns_none_when_any_edit_is_unlocatable() -> None:
    assert edit_ranges(TEXT, "missing") is None
    assert edit_ranges(TEXT, ["alpha", "missing"]) is None
    assert edit_ranges(TEXT, "") is None
    assert edit_ranges(TEXT, []) is None


def test_overlaps_uses_inclusive_interval_intersection() -> None:
    ranges = [(3, 5), (9, 9)]

    assert overlaps(5, 7, ranges)
    assert overlaps(1, 3, ranges)
    assert overlaps(9, 9, ranges)
    assert not overlaps(6, 8, ranges)
    assert not overlaps(1, 2, [])


def test_merge_ranges_coalesces_overlapping_and_adjacent() -> None:
    assert merge_ranges([(5, 6), (1, 2), (3, 3), (5, 9)]) == [(1, 3), (5, 9)]


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def make_git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "--quiet")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test")
    return repo


def commit_file(repo: Path, name: str, content: str) -> Path:
    file = repo / name
    file.write_text(content)
    git(repo, "add", name)
    git(repo, "commit", "--quiet", "-m", f"add {name}")
    return file


def test_diff_ranges_reports_changed_and_inserted_lines(tmp_path: Path) -> None:
    repo = make_git_repo(tmp_path)
    file = commit_file(repo, "src.py", "a = 1\nb = 2\nc = 3\nd = 4\n")

    file.write_text("a = 1\nb = 20\nc = 3\nd = 4\ne = 5\nf = 6\n")

    assert diff_ranges(repo, file) == [(2, 2), (5, 6)]


def test_diff_ranges_is_empty_for_an_unchanged_tracked_file(tmp_path: Path) -> None:
    repo = make_git_repo(tmp_path)
    file = commit_file(repo, "src.py", "a = 1\n")

    assert diff_ranges(repo, file) == []


def test_diff_ranges_ignores_pure_deletions(tmp_path: Path) -> None:
    repo = make_git_repo(tmp_path)
    file = commit_file(repo, "src.py", "a = 1\nb = 2\nc = 3\n")

    file.write_text("a = 1\nc = 3\n")

    assert diff_ranges(repo, file) == []


def test_diff_ranges_is_none_for_an_untracked_file(tmp_path: Path) -> None:
    repo = make_git_repo(tmp_path)
    commit_file(repo, "src.py", "a = 1\n")
    untracked = repo / "new.py"
    untracked.write_text("b = 2\n")

    assert diff_ranges(repo, untracked) is None


def test_diff_ranges_is_none_outside_a_git_repository(tmp_path: Path) -> None:
    file = tmp_path / "src.py"
    file.write_text("a = 1\n")

    assert diff_ranges(tmp_path, file) is None


def test_diff_ranges_is_none_before_the_first_commit(tmp_path: Path) -> None:
    repo = make_git_repo(tmp_path)
    file = repo / "src.py"
    file.write_text("a = 1\n")
    git(repo, "add", "src.py")

    assert diff_ranges(repo, file) is None
