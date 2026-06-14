from pathlib import Path

import pytest
from support import make_repo, mirror, write_global_rule, write_rule

from byor.cli import main


def test_exclude_removes_copy_and_include_restores_it(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_repo(home)
    canonical = write_global_rule(home, "no-cast.yml", "no-cast")
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


def test_include_leaves_rule_skipped_when_project_owns_the_id(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_repo(home)
    write_global_rule(home, "no-cast.yml", "no-cast")
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


def test_exclude_requires_an_initialized_repo(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = home / "bare"
    repo.mkdir()

    assert main(["exclude", "--repo", str(repo), "no-cast"]) == 1

    captured = capsys.readouterr()
    assert "byor init" in captured.err
    assert "Traceback" not in captured.err
    assert not (repo / ".byor").exists()
