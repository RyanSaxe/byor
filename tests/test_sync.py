import shutil
from pathlib import Path

import pytest

from byolsp.cli import main

RULE_TEMPLATE = (
    "id: {rule_id}\n"
    "language: Python\n"
    "message: {message}\n"
    "rule:\n"
    "  pattern: cast($TYPE, $VALUE)\n"
)


@pytest.fixture
def home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """A sandbox holding repos and the global config dir (via XDG_CONFIG_HOME)."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    return tmp_path


def make_repo(home: Path, name: str = "repo") -> Path:
    repo = home / name
    repo.mkdir()
    assert main(["init", "--repo", str(repo), "--non-interactive"]) == 0
    return repo


def write_rule(path: Path, rule_id: str, message: str = "Avoid this.") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(RULE_TEMPLATE.format(rule_id=rule_id, message=message))
    return path


def write_global_rule(home: Path, relpath: str, rule_id: str) -> Path:
    return write_rule(home / "xdg" / "byolsp" / "rules" / relpath, rule_id)


def mirror(repo: Path) -> Path:
    return repo / ".byolsp" / "rules" / "personal" / "global"


def sync(repo: Path, *extra: str) -> int:
    return main(["sync", "--repo", str(repo), *extra])


def test_sync_copies_global_rules_preserving_relative_paths(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_repo(home)
    canonical = write_global_rule(home, "python/no-cast.yml", "no-cast")
    capsys.readouterr()

    assert sync(repo) == 0

    copy = mirror(repo) / "python" / "no-cast.yml"
    assert copy.read_text() == canonical.read_text()
    assert f"Synced 1 global rule into {repo}" in capsys.readouterr().out


def test_sync_updates_changed_copies(home: Path) -> None:
    repo = make_repo(home)
    canonical = write_global_rule(home, "no-cast.yml", "no-cast")
    sync(repo)

    write_rule(canonical, "no-cast", message="Updated message.")
    assert sync(repo) == 0

    assert (mirror(repo) / "no-cast.yml").read_text() == canonical.read_text()


def test_sync_removes_deleted_rules_and_prunes_empty_dirs(home: Path) -> None:
    repo = make_repo(home)
    canonical = write_global_rule(home, "python/no-cast.yml", "no-cast")
    sync(repo)

    canonical.unlink()
    assert sync(repo) == 0

    assert not (mirror(repo) / "python").exists()
    assert (mirror(repo) / ".gitkeep").is_file()


def test_sync_mirrors_wholesale_but_leaves_non_yaml_alone(home: Path) -> None:
    """The mirror is a build artifact: hand edits and strays go, .gitkeep stays."""
    repo = make_repo(home)
    canonical = write_global_rule(home, "no-cast.yml", "no-cast")
    sync(repo)

    (mirror(repo) / "no-cast.yml").write_text("# hand edit\n")
    write_rule(mirror(repo) / "stray.yml", "stray")
    (mirror(repo) / "notes.md").write_text("not a rule\n")
    assert sync(repo) == 0

    assert (mirror(repo) / "no-cast.yml").read_text() == canonical.read_text()
    assert not (mirror(repo) / "stray.yml").exists()
    assert (mirror(repo) / "notes.md").read_text() == "not a rule\n"


def test_excluded_rule_is_skipped_and_its_copy_removed(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_repo(home)
    write_global_rule(home, "no-cast.yml", "no-cast")
    sync(repo)

    (repo / ".byolsp" / "local.yml").write_text(
        "version: 1\nglobal:\n  excluded_rule_ids:\n    - no-cast\n"
    )
    capsys.readouterr()
    assert sync(repo) == 0

    assert not (mirror(repo) / "no-cast.yml").exists()
    out = capsys.readouterr().out
    assert f"Synced 0 global rules into {repo}" in out
    assert "Skipped 1 global rule:" in out
    assert "  no-cast: excluded in .byolsp/local.yml" in out


def test_project_rule_with_same_id_suppresses_global_copy(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_repo(home)
    write_global_rule(home, "no-cast.yml", "no-cast")
    write_rule(repo / ".byolsp" / "rules" / "project" / "no-cast.yml", "no-cast")
    capsys.readouterr()

    assert sync(repo) == 0

    assert not (mirror(repo) / "no-cast.yml").exists()
    assert "  no-cast: overridden by project rule" in capsys.readouterr().out


def test_local_rule_with_same_id_suppresses_global_copy(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_repo(home)
    write_global_rule(home, "no-cast.yml", "no-cast")
    write_rule(
        repo / ".byolsp" / "rules" / "personal" / "local" / "no-cast.yml", "no-cast"
    )
    capsys.readouterr()

    assert sync(repo) == 0

    assert not (mirror(repo) / "no-cast.yml").exists()
    assert "  no-cast: overridden by local rule" in capsys.readouterr().out


def test_duplicate_canonical_global_ids_fail_cleanly(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_repo(home)
    write_global_rule(home, "a.yml", "no-cast")
    write_global_rule(home, "b.yml", "no-cast")

    assert sync(repo) == 1

    captured = capsys.readouterr()
    assert "Duplicate rule IDs" in captured.err
    assert "Traceback" not in captured.err


def test_sync_check_reports_staleness_without_writing(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_repo(home)
    write_global_rule(home, "no-cast.yml", "no-cast")
    capsys.readouterr()

    assert sync(repo, "--check") == 3

    assert not (mirror(repo) / "no-cast.yml").exists()
    assert f"Sync is stale in {repo}" in capsys.readouterr().out

    sync(repo)
    assert sync(repo, "--check") == 0
    assert f"Sync is fresh in {repo}" in capsys.readouterr().out


def test_init_syncs_existing_global_rules(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_global_rule(home, "python/no-cast.yml", "no-cast")

    repo = make_repo(home)

    assert (mirror(repo) / "python" / "no-cast.yml").is_file()
    assert "Synced 1 updated global rule" in capsys.readouterr().out


def test_any_command_self_heals_a_stale_repo(
    home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Running byolsp *anything* makes this repo correct (SPEC 3)."""
    repo = make_repo(home)
    write_global_rule(home, "no-cast.yml", "no-cast")
    monkeypatch.chdir(repo)
    capsys.readouterr()

    main(["list"])

    assert (mirror(repo) / "no-cast.yml").is_file()
    assert "byolsp: synced 1 updated global rule\n" in capsys.readouterr().out

    main(["list"])
    assert "byolsp: synced" not in capsys.readouterr().out


def test_sync_all_syncs_registered_repos_and_skips_missing_paths(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    first = make_repo(home, "first")
    second = make_repo(home, "second")
    gone = make_repo(home, "gone")
    shutil.rmtree(gone)
    write_global_rule(home, "no-cast.yml", "no-cast")
    capsys.readouterr()

    assert main(["sync", "--all"]) == 0

    assert (mirror(first) / "no-cast.yml").is_file()
    assert (mirror(second) / "no-cast.yml").is_file()
    captured = capsys.readouterr()
    assert f"byolsp: skipping {gone}: path no longer exists" in captured.err
    assert f"Synced 1 global rule into {first}" in captured.out
    assert f"Synced 1 global rule into {second}" in captured.out
