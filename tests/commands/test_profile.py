"""Exercise profile command behavior.

Profiles are named exclusion bundles defined in the global config; applying one translates its
selectors into this repo's local exclusions and re-syncs the mirror. The tests pin that existing
exclusions survive, re-application is idempotent, and unknown profiles or an uninitialized repo fail
cleanly.
"""

from pathlib import Path

import pytest
from support import make_repo, write_global_rule

from byor.cli import main
from byor.config import (
    GlobalConfig,
    ProfileConfig,
    load_local_config,
    save_global_config,
)


def test_profile_list_shows_configured_profiles(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    save_global_config(
        home / "xdg" / "byor",
        GlobalConfig(
            profiles={
                "existing": ProfileConfig(description="Low-friction defaults."),
                "minimal": ProfileConfig(),
            }
        ),
    )

    assert main(["profile", "list"]) == 0

    out = capsys.readouterr().out
    assert "existing  Low-friction defaults." in out
    assert "minimal" in out


def test_profile_add_applies_selectors_and_syncs(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = make_repo(home)
    write_global_rule(home, "no-cast.yml", rule_id="no-cast", tags=["legacy-risk"])
    save_global_config(
        home / "xdg" / "byor",
        GlobalConfig(
            profiles={
                "existing": ProfileConfig(
                    excluded_rule_tags=["legacy-risk"],
                    excluded_checks=["ty"],
                )
            }
        ),
    )
    main(["sync", "--repo", str(repo)])
    capsys.readouterr()

    assert main(["profile", "add", "existing", "--repo", str(repo)]) == 0

    local = load_local_config(repo)
    assert local.excluded_rule_tags == ["legacy-risk"]
    assert local.excluded_checks == ["ty"]
    assert not (repo / ".byor" / "rules" / "personal" / "global" / "no-cast.yml").exists()
    out = capsys.readouterr().out
    assert "Added profile 'existing' to .byor/local.yml" in out
    assert "Synced 1 removed global rule" in out


def test_profile_add_preserves_existing_exclusions_and_is_idempotent(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_repo(home)
    save_global_config(
        home / "xdg" / "byor",
        GlobalConfig(profiles={"existing": ProfileConfig(excluded_rule_tags=["legacy-risk"])}),
    )
    main(["exclude", "--repo", str(repo), "--check", "mypy"])
    capsys.readouterr()

    assert main(["profile", "add", "existing", "--repo", str(repo)]) == 0
    assert main(["profile", "add", "existing", "--repo", str(repo)]) == 0

    local = load_local_config(repo)
    assert local.excluded_checks == ["mypy"]  # manual exclusion survives
    assert local.excluded_rule_tags == ["legacy-risk"]  # added once, not duplicated


def test_profile_add_reports_unknown_profile(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = make_repo(home)
    capsys.readouterr()

    assert main(["profile", "add", "missing", "--repo", str(repo)]) == 1

    captured = capsys.readouterr()
    assert "unknown profile 'missing'" in captured.err
    assert "Traceback" not in captured.err


def test_profile_add_requires_initialized_repo(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = home / "bare"
    repo.mkdir()
    save_global_config(
        home / "xdg" / "byor",
        GlobalConfig(profiles={"existing": ProfileConfig()}),
    )

    assert main(["profile", "add", "existing", "--repo", str(repo)]) == 1

    captured = capsys.readouterr()
    assert "byor init" in captured.err
    assert "Traceback" not in captured.err
    assert not (repo / ".byor").exists()
