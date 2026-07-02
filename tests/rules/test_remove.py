"""Exercise rule removal behavior.

Removal is scope-aware: deleting a project rule that shadowed a global id lets the global copy sync
back in, while removing a global rule deletes the canonical file and fans the deletion out to
registered repos. Unknown rule ids fail cleanly instead of guessing.
"""

from pathlib import Path

import pytest
from support import make_repo, mirror, write_global_rule, write_rule

from byor.cli import main


def test_remove_deletes_a_project_rule(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = make_repo(home)
    target = write_rule(repo / ".byor" / "rules" / "project" / "no-cast.yml", "no-cast")
    capsys.readouterr()

    assert main(["remove", "--repo", str(repo), "no-cast"]) == 0

    assert not target.exists()
    out = capsys.readouterr().out
    assert "Removed project rule 'no-cast' at .byor/rules/project/no-cast.yml" in out


def test_remove_shadowing_project_rule_lets_the_global_copy_return(home: Path) -> None:
    repo = make_repo(home)
    write_global_rule(home, "no-cast.yml", rule_id="no-cast")
    write_rule(repo / ".byor" / "rules" / "project" / "no-cast.yml", "no-cast")
    main(["sync", "--repo", str(repo)])
    assert not (mirror(repo) / "no-cast.yml").exists()  # shadowed: copy skipped

    assert main(["remove", "--repo", str(repo), "no-cast"]) == 0

    assert (mirror(repo) / "no-cast.yml").is_file()


def test_remove_global_rule_deletes_the_canonical_file_and_fans_out(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    first = make_repo(home, name="first")
    second = make_repo(home, name="second")
    canonical = write_global_rule(home, "python/no-cast.yml", rule_id="no-cast")
    main(["sync", "--all"])
    capsys.readouterr()

    assert main(["remove", "--repo", str(first), "no-cast", "--scope", "global"]) == 0

    assert not canonical.exists()
    assert not (mirror(first) / "python" / "no-cast.yml").exists()
    assert not (mirror(second) / "python" / "no-cast.yml").exists()
    out = capsys.readouterr().out
    assert f"Removed global rule 'no-cast' at {canonical}" in out
    assert f"Synced 1 removed global rule into {second}" in out


def test_remove_unknown_rule_id_fails_cleanly(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = make_repo(home)

    assert main(["remove", "--repo", str(repo), "missing"]) == 1

    captured = capsys.readouterr()
    assert "No rule with ID 'missing' found in any scope." in captured.err
    assert "Traceback" not in captured.err
