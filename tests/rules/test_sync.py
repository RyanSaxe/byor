"""Exercise global rule sync behavior.

Sync mirrors canonical global rules into the repo — preserving relative paths, updating changed
copies, pruning deletions — while leaving non-YAML files alone and refusing a mirror path outside
the repo. Exclusions and same-id project or local rules suppress copies, --check reports staleness
without writing, and every command self-heals a stale repo on entry, skipping agents whose config is
broken.
"""

import shutil
from pathlib import Path

import pytest
from support import (
    install_agents,
    install_package,
    make_repo,
    mirror,
    write_global_rule,
    write_package_rule,
    write_rule,
)

from byor.cli import main
from byor.config import (
    load_global_config,
    load_repo_config,
    load_repo_registry,
    repo_registry_path,
    save_repo_config,
)
from byor.io.paths import global_config_dir
from byor.rules.sync import mirror_contents, mirror_global_rules


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
    write_global_rule(home, "no-cast.yml", rule_id="no-cast")
    repo = make_repo(home)  # init syncs the global copy in
    write_rule(repo / ".byor" / "rules" / "project" / "no-cast.yml", "no-cast")
    capsys.readouterr()

    assert main(sync_args(repo)) == 0

    assert not (mirror(repo) / "no-cast.yml").exists()
    assert "  no-cast: overridden by project rule" in capsys.readouterr().out


def test_local_rule_with_same_id_suppresses_global_copy(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write_global_rule(home, "no-cast.yml", rule_id="no-cast")
    repo = make_repo(home)  # init syncs the global copy in
    write_rule(repo / ".byor" / "rules" / "personal" / "local" / "no-cast.yml", "no-cast")
    capsys.readouterr()

    assert main(sync_args(repo)) == 0

    assert not (mirror(repo) / "no-cast.yml").exists()
    assert "  no-cast: overridden by local rule" in capsys.readouterr().out


# The post-merge/post-checkout shims run `byor sync` on every pull, so a
# steady-state sync must not narrate forever: when nothing changed it prints
# nothing (silence is the unix success signal); `byor list` keeps skips
# visible on demand.
def test_steady_state_sync_prints_nothing(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write_global_rule(home, "no-cast.yml", rule_id="no-cast")
    repo = make_repo(home)
    write_rule(repo / ".byor" / "rules" / "project" / "no-cast.yml", "no-cast")
    assert main(sync_args(repo)) == 0  # removes the mirrored copy and reports the skip
    capsys.readouterr()

    assert main(sync_args(repo)) == 0

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


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
    # monkeypatch isolates process state (env, cwd, stdio): an external boundary
    # ast-grep-ignore: python.question-mocks
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


# heal.py promises healing degrades to warnings; a malformed ~/sgconfig.yml
# used to abort every self-healing command before it could run.
def test_self_heal_warns_instead_of_crashing_on_a_broken_home_sgconfig(
    home: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = make_repo(home)
    (home / "sgconfig.yml").write_text("ruleDirs: not-a-list\n")
    capsys.readouterr()

    assert main(["list", "--repo", str(repo)]) == 0

    err = capsys.readouterr().err
    assert "byor: skipping ~/sgconfig.yml self-heal" in err
    assert "run 'byor doctor'" in err


# heal_gate used to raise on a broken repo config, discarding the warning
# heal_repo had already collected and crashing the command with a bare error.
def test_self_heal_degrades_gate_heal_and_keeps_the_repo_warning(
    home: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = make_repo(home)
    config_path = repo / ".byor" / "config.yml"
    config_path.write_text(config_path.read_text() + "fail_on: sometimes\n")
    capsys.readouterr()

    assert main(["list", "--repo", str(repo)]) == 1  # list itself still needs the config

    err = capsys.readouterr().err
    assert "byor: skipping repo self-heal" in err
    assert "byor: skipping gate self-heal" in err


# On a case-insensitive filesystem the stale old name resolves to the freshly
# written file, so removing after writing used to delete the renamed rule.
def test_mirror_converges_after_a_case_only_rename(tmp_path: Path) -> None:
    mirror_dir = tmp_path / "mirror"
    content = "id: no-print\n"
    assert mirror_global_rules(mirror_dir, {"no-print.yml": content}).written == 1

    result = mirror_global_rules(mirror_dir, {"No-Print.yml": content})

    assert result.written == 1
    assert mirror_contents(mirror_dir) == {"No-Print.yml": content}


def test_sync_all_continues_past_a_repo_whose_sync_fails(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    wedged = make_repo(home, name="wedged")
    write_package_rule(home, "pkg-a", relpath="dup.yml", rule_id="dup-id")
    write_package_rule(home, "pkg-b", relpath="dup.yml", rule_id="dup-id")
    install_package(wedged, "pkg-a")
    install_package(wedged, "pkg-b")
    healthy = make_repo(home, name="healthy")
    write_global_rule(home, "no-cast.yml", rule_id="no-cast")
    capsys.readouterr()

    assert main(["sync", "--all"]) == 1

    captured = capsys.readouterr()
    assert (mirror(healthy) / "no-cast.yml").is_file()
    assert f"byor: skipping {wedged}: Duplicate rule IDs" in captured.err
    assert f"Synced 1 global rule into {healthy}" in captured.out


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


def registered_repos() -> list[Path]:
    config_dir = global_config_dir()
    return load_repo_registry(repo_registry_path(config_dir, load_global_config(config_dir)))


def test_sync_all_prune_drops_only_nonexistent_registry_entries(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    kept = make_repo(home, name="kept")
    parked = make_repo(home, name="parked")
    gone = make_repo(home, name="gone")
    shutil.rmtree(gone)
    # Disabled-but-existing repos stay registered; only dead paths are pruned.
    assert main(["disable", str(parked)]) == 0
    capsys.readouterr()

    assert main(["sync", "--all", "--prune"]) == 0

    captured = capsys.readouterr()
    assert f"Pruned {gone} from the registry" in captured.out
    assert "path no longer exists" not in captured.err
    assert registered_repos() == [kept.resolve(), parked.resolve()]

    # With the dead entry gone, doctor's registry check is green again.
    assert main(["doctor", "--repo", str(kept)]) == 0
    assert "ok    registered_repos" in capsys.readouterr().out


def test_sync_prune_requires_all_and_refuses_check(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = make_repo(home)
    capsys.readouterr()

    assert main(["sync", "--repo", str(repo), "--prune"]) == 1
    assert "--prune requires --all" in capsys.readouterr().err

    assert main(["sync", "--all", "--check", "--prune"]) == 1
    assert "--prune cannot be combined with --check" in capsys.readouterr().err
