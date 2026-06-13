from pathlib import Path

import pytest
from conftest import make_repo, mirror, write_global_rule, write_rule

from byor.cli import main


def promote(repo: Path, rule_id: str, source: str, *extra: str) -> int:
    return main(["promote", "--repo", str(repo), rule_id, "--from", source, *extra])


def test_promote_local_moves_the_rule_preserving_its_relative_path(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_repo(home)
    local = write_rule(
        repo / ".byor" / "rules" / "personal" / "local" / "python" / "exp.yml", "exp"
    )
    content = local.read_text()
    capsys.readouterr()

    assert promote(repo, "exp", "local") == 0

    destination = repo / ".byor" / "rules" / "project" / "python" / "exp.yml"
    assert destination.read_text() == content
    assert not local.exists()
    out = capsys.readouterr().out
    assert "Promoted 'exp' to .byor/rules/project/python/exp.yml" in out


def test_promote_local_with_keep_local_fails_before_writing(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_repo(home)
    local = write_rule(
        repo / ".byor" / "rules" / "personal" / "local" / "exp.yml", "exp"
    )

    assert promote(repo, "exp", "local", "--keep-local") == 1

    assert local.is_file()
    assert not (repo / ".byor" / "rules" / "project" / "exp.yml").exists()
    err = capsys.readouterr().err
    assert "A local variation of a project rule requires a different ID." in err


def test_promote_global_copies_without_touching_canonical_or_exclusions(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_repo(home)
    canonical = write_global_rule(home, "no-cast.yml", "no-cast")
    main(["sync", "--repo", str(repo)])
    capsys.readouterr()

    assert promote(repo, "no-cast", "global") == 0

    destination = repo / ".byor" / "rules" / "project" / "no-cast.yml"
    assert destination.read_text() == canonical.read_text()
    assert canonical.is_file()
    assert not (mirror(repo) / "no-cast.yml").exists()
    assert "excluded_rule_ids: []" in (repo / ".byor" / "local.yml").read_text()
    assert f"Synced 1 removed global rule into {repo}" in capsys.readouterr().out


def test_promote_requires_replace_to_overwrite_the_destination(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_repo(home)
    # The destination file exists but holds a different rule ID, so the repo
    # is healthy before promote and only the file path collides.
    existing = write_rule(
        repo / ".byor" / "rules" / "project" / "exp.yml", "old-exp", message="Old."
    )
    local = write_rule(
        repo / ".byor" / "rules" / "personal" / "local" / "exp.yml",
        "exp",
        message="New.",
    )
    promoted_content = local.read_text()

    assert promote(repo, "exp", "local") == 1
    assert "rerun with --replace" in capsys.readouterr().err
    assert "Old." in existing.read_text()
    assert local.is_file()

    assert promote(repo, "exp", "local", "--replace") == 0
    assert existing.read_text() == promoted_content
    assert not local.exists()


def test_promote_unknown_rule_id_fails_cleanly(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_repo(home)

    assert promote(repo, "missing", "global") == 1

    captured = capsys.readouterr()
    assert "No rule with ID 'missing' found in global rules." in captured.err
    assert "Traceback" not in captured.err
