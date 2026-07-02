"""Exercise global rule sync behavior.

These tests document the public behavior expected from the surrounding package area. Keeping that
intent at module scope helps the dogfooding contract distinguish purposeful coverage from incidental
implementation checks.
"""

import shutil
from pathlib import Path

import pytest
from support import install_agents, make_repo, mirror, write_global_rule, write_rule

from byor.cli import main
from byor.config import load_repo_config, save_repo_config


def sync_args(repo: Path, *extra: str) -> list[str]:
    return ["sync", "--repo", str(repo), *extra]


def test_sync_refuses_a_mirror_path_outside_the_repo(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = make_repo(home)
    write_global_rule(home, "no-cast.yml", rule_id="no-cast")
    protected = home / "outside" / "protected.yml"
    protected.parent.mkdir(parents=True)
    protected.write_text("keep me\n")
    config = load_repo_config(repo)
    config.paths.personal_global_rules = "../outside"
    save_repo_config(repo, config)
    capsys.readouterr()

    assert main(sync_args(repo)) == 1

    assert protected.read_text() == "keep me\n"
    assert "Traceback" not in capsys.readouterr().err


def test_sync_copies_global_rules_preserving_relative_paths(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = make_repo(home)
    canonical = write_global_rule(home, "python/no-cast.yml", rule_id="no-cast")
    capsys.readouterr()

    assert main(sync_args(repo)) == 0

    copy = mirror(repo) / "python" / "no-cast.yml"
    assert copy.read_text() == canonical.read_text()
    assert f"Synced 1 global rule into {repo}" in capsys.readouterr().out


def test_sync_updates_changed_copies(home: Path) -> None:
    repo = make_repo(home)
    canonical = write_global_rule(home, "no-cast.yml", rule_id="no-cast")
    main(sync_args(repo))

    write_rule(canonical, "no-cast", message="Updated message.")
    assert main(sync_args(repo)) == 0

    assert (mirror(repo) / "no-cast.yml").read_text() == canonical.read_text()


def test_sync_removes_deleted_rules_and_prunes_empty_dirs(home: Path) -> None:
    repo = make_repo(home)
    canonical = write_global_rule(home, "python/no-cast.yml", rule_id="no-cast")
    main(sync_args(repo))

    canonical.unlink()
    assert main(sync_args(repo)) == 0

    assert not (mirror(repo) / "python").exists()
    assert (mirror(repo) / ".gitkeep").is_file()


def test_sync_mirrors_wholesale_but_leaves_non_yaml_alone(home: Path) -> None:
    repo = make_repo(home)
    canonical = write_global_rule(home, "no-cast.yml", rule_id="no-cast")
    main(sync_args(repo))

    (mirror(repo) / "no-cast.yml").write_text("# hand edit\n")
    write_rule(mirror(repo) / "stray.yml", "stray")
    (mirror(repo) / "notes.md").write_text("not a rule\n")
    assert main(sync_args(repo)) == 0

    assert (mirror(repo) / "no-cast.yml").read_text() == canonical.read_text()
    assert not (mirror(repo) / "stray.yml").exists()
    assert (mirror(repo) / "notes.md").read_text() == "not a rule\n"


def test_excluded_rule_is_skipped_and_its_copy_removed(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = make_repo(home)
    write_global_rule(home, "no-cast.yml", rule_id="no-cast")
    main(sync_args(repo))

    (repo / ".byor" / "local.yml").write_text("version: 1\nglobal:\n  excluded_rule_ids:\n    - no-cast\n")
    capsys.readouterr()
    assert main(sync_args(repo)) == 0

    assert not (mirror(repo) / "no-cast.yml").exists()
    out = capsys.readouterr().out
    assert f"Synced 0 global rules into {repo}" in out
    assert "Skipped 1 global rule:" in out
    assert "  no-cast: excluded in .byor/local.yml" in out


def test_rule_with_excluded_tag_is_skipped_and_its_copy_removed(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = make_repo(home)
    write_global_rule(home, "no-cast.yml", rule_id="no-cast", tags=["legacy-risk"])
    main(sync_args(repo))

    (repo / ".byor" / "local.yml").write_text("version: 1\nglobal:\n  excluded_tags:\n    - legacy-risk\n")
    capsys.readouterr()
    assert main(sync_args(repo)) == 0

    assert not (mirror(repo) / "no-cast.yml").exists()
    out = capsys.readouterr().out
    assert "  no-cast: excluded by tag 'legacy-risk' in .byor/local.yml" in out


def test_project_rule_with_same_id_suppresses_global_copy(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = make_repo(home)
    write_global_rule(home, "no-cast.yml", rule_id="no-cast")
    write_rule(repo / ".byor" / "rules" / "project" / "no-cast.yml", "no-cast")
    capsys.readouterr()

    assert main(sync_args(repo)) == 0

    assert not (mirror(repo) / "no-cast.yml").exists()
    assert "  no-cast: overridden by project rule" in capsys.readouterr().out


def test_local_rule_with_same_id_suppresses_global_copy(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = make_repo(home)
    write_global_rule(home, "no-cast.yml", rule_id="no-cast")
    write_rule(repo / ".byor" / "rules" / "personal" / "local" / "no-cast.yml", "no-cast")
    capsys.readouterr()

    assert main(sync_args(repo)) == 0

    assert not (mirror(repo) / "no-cast.yml").exists()
    assert "  no-cast: overridden by local rule" in capsys.readouterr().out


def test_duplicate_canonical_global_ids_fail_cleanly(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = make_repo(home)
    write_global_rule(home, "a.yml", rule_id="no-cast")
    write_global_rule(home, "b.yml", rule_id="no-cast")

    assert main(sync_args(repo)) == 1

    captured = capsys.readouterr()
    assert "Duplicate rule IDs" in captured.err
    assert "Traceback" not in captured.err


def test_sync_check_reports_staleness_without_writing(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = make_repo(home)
    write_global_rule(home, "no-cast.yml", rule_id="no-cast")
    capsys.readouterr()

    assert main(sync_args(repo, "--check")) == 3

    assert not (mirror(repo) / "no-cast.yml").exists()
    assert f"Sync is stale in {repo}" in capsys.readouterr().out

    main(sync_args(repo))
    assert main(sync_args(repo, "--check")) == 0
    assert f"Sync is fresh in {repo}" in capsys.readouterr().out


def test_sync_check_reports_staleness_after_excluding_a_tag(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = make_repo(home)
    write_global_rule(home, "no-cast.yml", rule_id="no-cast", tags=["legacy-risk"])
    main(sync_args(repo))

    (repo / ".byor" / "local.yml").write_text("version: 1\nglobal:\n  excluded_tags:\n    - legacy-risk\n")
    capsys.readouterr()

    assert main(sync_args(repo, "--check")) == 3
    assert (mirror(repo) / "no-cast.yml").exists()
    assert f"Sync is stale in {repo}" in capsys.readouterr().out


def test_init_syncs_existing_global_rules(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write_global_rule(home, "python/no-cast.yml", rule_id="no-cast")

    repo = make_repo(home)

    assert (mirror(repo) / "python" / "no-cast.yml").is_file()
    assert "Synced 1 updated global rule" in capsys.readouterr().out


def test_any_command_self_heals_a_stale_repo(
    home: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = make_repo(home)
    write_global_rule(home, "no-cast.yml", rule_id="no-cast")
    monkeypatch.chdir(repo)
    capsys.readouterr()

    main(["list"])

    assert (mirror(repo) / "no-cast.yml").is_file()
    assert "byor: synced 1 updated global rule\n" in capsys.readouterr().err

    main(["list"])
    assert "byor: synced" not in capsys.readouterr().err


def test_self_heal_skips_an_agent_with_a_broken_config(
    home: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = make_repo(home)
    install_agents("claude-code")
    (home / ".claude" / "settings.json").write_text("{not json")
    capsys.readouterr()

    assert main(["list", "--repo", str(repo)]) == 0

    err = capsys.readouterr().err
    assert "byor: skipping claude-code self-heal" in err
    assert "run 'byor doctor'" in err


def test_sync_all_syncs_registered_repos_and_skips_missing_paths(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    first = make_repo(home, name="first")
    second = make_repo(home, name="second")
    gone = make_repo(home, name="gone")
    shutil.rmtree(gone)
    write_global_rule(home, "no-cast.yml", rule_id="no-cast")
    capsys.readouterr()

    assert main(["sync", "--all"]) == 0

    assert (mirror(first) / "no-cast.yml").is_file()
    assert (mirror(second) / "no-cast.yml").is_file()
    captured = capsys.readouterr()
    assert f"byor: skipping {gone}: path no longer exists" in captured.err
    assert f"Synced 1 global rule into {first}" in captured.out
    assert f"Synced 1 global rule into {second}" in captured.out
