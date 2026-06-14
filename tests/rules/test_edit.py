from pathlib import Path

import pytest
from support import (
    NOOP_EDITOR,
    RULE_TEMPLATE,
    make_editor,
    make_repo,
    mirror,
    write_global_rule,
    write_rule,
)

from byor.cli import main


def edit_args(repo: Path, rule_id: str, *extra: str) -> list[str]:
    return ["edit", "--repo", str(repo), rule_id, *extra]


def test_edit_updates_a_project_rule_in_place(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_repo(home)
    target = write_rule(repo / ".byor" / "rules" / "project" / "no-cast.yml", "no-cast")
    replacement = RULE_TEMPLATE.format(rule_id="no-cast", message="Edited.")
    monkeypatch.setenv("EDITOR", make_editor(home, replacement))
    capsys.readouterr()

    assert main(edit_args(repo, "no-cast")) == 0

    assert target.read_text() == replacement
    out = capsys.readouterr().out
    assert "Updated project rule 'no-cast' at .byor/rules/project/no-cast.yml" in out


def test_edit_auto_prefers_project_over_global(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(home)
    canonical = write_global_rule(home, "no-cast.yml", "no-cast")
    project = write_rule(
        repo / ".byor" / "rules" / "project" / "no-cast.yml", "no-cast"
    )
    replacement = RULE_TEMPLATE.format(rule_id="no-cast", message="Edited.")
    monkeypatch.setenv("EDITOR", make_editor(home, replacement))

    assert main(edit_args(repo, "no-cast")) == 0

    assert project.read_text() == replacement
    assert canonical.read_text() != replacement


def test_edit_global_opens_the_canonical_rule_and_fans_out(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    first = make_repo(home, "first")
    second = make_repo(home, "second")
    canonical = write_global_rule(home, "python/no-cast.yml", "no-cast")
    main(["sync", "--all"])
    replacement = RULE_TEMPLATE.format(rule_id="no-cast", message="Edited.")
    monkeypatch.setenv("EDITOR", make_editor(home, replacement))
    capsys.readouterr()

    assert main(edit_args(first, "no-cast")) == 0

    assert canonical.read_text() == replacement
    assert (mirror(first) / "python" / "no-cast.yml").read_text() == replacement
    assert (mirror(second) / "python" / "no-cast.yml").read_text() == replacement
    out = capsys.readouterr().out
    assert f"Updated global rule 'no-cast' at {canonical}" in out
    assert f"Synced 1 updated global rule into {second}" in out


def test_edit_unknown_rule_id_fails_cleanly(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_repo(home)

    assert main(edit_args(repo, "missing")) == 1

    captured = capsys.readouterr()
    assert "No rule with ID 'missing' found in any scope." in captured.err
    assert "Traceback" not in captured.err


def test_edit_rejects_an_invalid_result_and_keeps_the_original(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_repo(home)
    target = write_rule(repo / ".byor" / "rules" / "project" / "no-cast.yml", "no-cast")
    original = target.read_text()
    monkeypatch.setenv("EDITOR", make_editor(home, "id: no-cast\n"))

    assert main(edit_args(repo, "no-cast")) == 1

    assert target.read_text() == original
    err = capsys.readouterr().err
    assert "missing required ast-grep fields" in err
    assert "Your draft is saved at" in err


def test_edit_with_no_changes_is_a_quiet_no_op(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_repo(home)
    target = write_rule(repo / ".byor" / "rules" / "project" / "no-cast.yml", "no-cast")
    original = target.read_text()
    monkeypatch.setenv("EDITOR", NOOP_EDITOR)
    capsys.readouterr()

    assert main(edit_args(repo, "no-cast")) == 0

    assert target.read_text() == original
    assert "No changes to 'no-cast'" in capsys.readouterr().out


def test_edit_rejects_an_id_change_that_collides_with_another_scope(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_repo(home)
    write_rule(repo / ".byor" / "rules" / "project" / "no-cast.yml", "no-cast")
    local = write_rule(
        repo / ".byor" / "rules" / "personal" / "local" / "mine.yml", "mine"
    )
    original = local.read_text()
    replacement = RULE_TEMPLATE.format(rule_id="no-cast", message="Edited.")
    monkeypatch.setenv("EDITOR", make_editor(home, replacement))

    assert main(edit_args(repo, "mine", "--scope", "local")) == 1

    assert local.read_text() == original
    err = capsys.readouterr().err
    assert "A local variation of a project rule requires a different ID." in err
    assert "Your draft is saved at" in err
