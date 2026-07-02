"""Exercise rule exclusion and inclusion behavior.

Exclusions are repo-local opt-outs recorded in local config; excluding removes the mirrored copy and
including restores it, unless a project rule owns the id and keeps it shadowed. Selectors cover
rules and checks by name or tag, and the command demands exactly one selector and an initialized
repo.
"""

from pathlib import Path

import pytest
from support import make_repo, mirror, write_global_rule, write_rule

from byor.cli import main
from byor.config import load_local_config


def test_exclude_removes_copy_and_include_restores_it(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = make_repo(home)
    canonical = write_global_rule(home, "no-cast.yml", rule_id="no-cast")
    main(["sync", "--repo", str(repo)])
    capsys.readouterr()

    assert main(["exclude", "--repo", str(repo), "no-cast"]) == 0

    assert not (mirror(repo) / "no-cast.yml").exists()
    assert "- no-cast" in (repo / ".byor" / "local.yml").read_text()
    out = capsys.readouterr().out
    assert "Excluded 'no-cast' in .byor/local.yml" in out
    assert f"Synced 1 removed global rule into {repo}" in out

    assert main(["include", "--repo", str(repo), "no-cast"]) == 0

    assert (mirror(repo) / "no-cast.yml").read_text() == canonical.read_text()
    assert "- no-cast" not in (repo / ".byor" / "local.yml").read_text()
    out = capsys.readouterr().out
    assert "Re-enabled 'no-cast'" in out
    assert f"Synced 1 updated global rule into {repo}" in out


def test_include_leaves_rule_skipped_when_project_owns_the_id(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = make_repo(home)
    write_global_rule(home, "no-cast.yml", rule_id="no-cast")
    write_rule(repo / ".byor" / "rules" / "project" / "no-cast.yml", "no-cast")
    main(["exclude", "--repo", str(repo), "no-cast"])
    capsys.readouterr()

    assert main(["include", "--repo", str(repo), "no-cast"]) == 0

    assert not (mirror(repo) / "no-cast.yml").exists()
    out = capsys.readouterr().out
    assert "'no-cast' is still skipped: overridden by project rule" in out


def test_exclude_is_idempotent(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = make_repo(home)
    main(["exclude", "--repo", str(repo), "no-cast"])
    capsys.readouterr()

    assert main(["exclude", "--repo", str(repo), "no-cast"]) == 0

    assert "'no-cast' is already excluded" in capsys.readouterr().out
    assert (repo / ".byor" / "local.yml").read_text().count("no-cast") == 1


def test_exclude_and_include_rule_tag(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = make_repo(home)
    write_global_rule(home, "no-cast.yml", rule_id="no-cast", tags=["legacy-risk"])
    main(["sync", "--repo", str(repo)])
    capsys.readouterr()

    assert main(["exclude", "--repo", str(repo), "--tag", "legacy-risk"]) == 0

    assert not (mirror(repo) / "no-cast.yml").exists()
    assert load_local_config(repo).excluded_rule_tags == ["legacy-risk"]
    assert "Excluded rule tag 'legacy-risk'" in capsys.readouterr().out

    assert main(["include", "--repo", str(repo), "--tag", "legacy-risk"]) == 0

    assert (mirror(repo) / "no-cast.yml").exists()
    assert load_local_config(repo).excluded_rule_tags == []
    assert "Re-enabled rule tag 'legacy-risk'" in capsys.readouterr().out


def test_exclude_and_include_check_selectors(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = make_repo(home)

    assert main(["exclude", "--repo", str(repo), "--check", "ty"]) == 0
    assert main(["exclude", "--repo", str(repo), "--check-tag", "strict"]) == 0

    local = load_local_config(repo)
    assert local.excluded_checks == ["ty"]
    assert local.excluded_check_tags == ["strict"]

    assert main(["include", "--repo", str(repo), "--check", "ty"]) == 0
    assert main(["include", "--repo", str(repo), "--check-tag", "strict"]) == 0

    local = load_local_config(repo)
    assert local.excluded_checks == []
    assert local.excluded_check_tags == []
    assert "Re-enabled check tag 'strict'" in capsys.readouterr().out


def test_exclude_requires_exactly_one_selector(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = make_repo(home)

    assert main(["exclude", "--repo", str(repo)]) == 1
    assert main(["exclude", "--repo", str(repo), "no-cast", "--tag", "legacy"]) == 1

    captured = capsys.readouterr()
    assert "choose exactly one" in captured.err


def test_exclude_requires_an_initialized_repo(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = home / "bare"
    repo.mkdir()

    assert main(["exclude", "--repo", str(repo), "no-cast"]) == 1

    captured = capsys.readouterr()
    assert "byor init" in captured.err
    assert "Traceback" not in captured.err
    assert not (repo / ".byor").exists()
