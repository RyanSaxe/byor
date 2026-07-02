"""Exercise rule creation behavior.

`byor add` accepts a rule from a file or an $EDITOR session, or prints the template when given
neither; the editor flows run real subprocess commands built by the fake-editor helpers in
support.py. Validation cases pin duplicate-id handling (an error within a scope, a legal override
across scopes), --allow-exceptions prefilling the standard escape-hatch sentence, and rule-id
hygiene including path-traversal rejection.
"""

from pathlib import Path

import pytest
from support import (
    NOOP_EDITOR,
    RULE_TEMPLATE,
    failing_editor,
    make_editor,
    make_repo,
    mirror,
    substituting_editor,
    write_global_rule,
    write_rule,
)

from byor.cli import main
from byor.io.yamlio import load_yaml_mapping
from byor.rules.rules import ALLOW_EXCEPTIONS_SENTENCE


def add_args(repo: Path, *extra: str) -> list[str]:
    return ["add", "--repo", str(repo), *extra]


def written_agent_prompt(path: Path) -> str:
    prompt = load_yaml_mapping(path)["metadata"]["byor"]["agent_prompt"]
    assert isinstance(prompt, str)
    return prompt


def test_add_without_source_prints_template_and_hint(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = make_repo(home)
    capsys.readouterr()

    assert main(add_args(repo, "--scope", "project")) == 0

    out = capsys.readouterr().out
    assert "id: REPLACE_ME" in out
    assert "language: Python" in out
    assert "agent_prompt: REPLACE_ME" in out
    assert "allow_with_comment" not in out
    assert "Rerun with --from FILE or --edit" in out

    assert main(add_args(repo, "--scope", "project", "--id", "no-cast", "--language", "Go")) == 0
    out = capsys.readouterr().out
    assert "id: no-cast" in out
    assert "language: Go" in out


def test_add_from_file_creates_project_rule(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = make_repo(home)
    source = write_rule(home / "source.yml", "team-rule")
    capsys.readouterr()

    assert main(add_args(repo, "--scope", "project", "--from", str(source))) == 0

    destination = repo / ".byor" / "rules" / "project" / "team-rule.yml"
    assert destination.read_text() == source.read_text()
    captured = capsys.readouterr()
    assert "Added project rule 'team-rule' at .byor/rules/project/team-rule.yml" in captured.out
    assert "doctor:" not in captured.out


def test_add_global_rule_fans_out_to_registered_repos(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    first = make_repo(home, name="first")
    second = make_repo(home, name="second")
    source = write_rule(home / "source.yml", "no-cast")
    capsys.readouterr()

    assert main(["add", "--repo", str(first), "--scope", "global", "--from", str(source)]) == 0

    canonical = home / "xdg" / "byor" / "rules" / "no-cast.yml"
    assert canonical.read_text() == source.read_text()
    assert (mirror(first) / "no-cast.yml").is_file()
    assert (mirror(second) / "no-cast.yml").is_file()
    out = capsys.readouterr().out
    assert f"Synced 1 updated global rule into {first}" in out
    assert f"Synced 1 updated global rule into {second}" in out


# monkeypatch isolates process state (env, cwd, stdio): an external boundary
# ast-grep-ignore: python.question-mocks
def test_add_edit_writes_the_edited_template(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = make_repo(home)
    content = RULE_TEMPLATE.format(rule_id="my-rule", message="No.")
    monkeypatch.setenv("EDITOR", make_editor(home, content))

    assert main(add_args(repo, "--scope", "local", "--edit")) == 0

    destination = repo / ".byor" / "rules" / "personal" / "local" / "my-rule.yml"
    assert destination.read_text() == content


def test_add_edit_aborts_when_template_left_unedited(
    home: Path,
    # monkeypatch isolates process state (env, cwd, stdio): an external boundary
    # ast-grep-ignore: python.question-mocks
    monkeypatch: pytest.MonkeyPatch,
    *,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = make_repo(home)
    monkeypatch.setenv("EDITOR", NOOP_EDITOR)

    assert main(add_args(repo, "--scope", "local", "--edit")) == 1

    assert "template was left unedited" in capsys.readouterr().err
    local_dir = repo / ".byor" / "rules" / "personal" / "local"
    assert list(local_dir.glob("*.yml")) == []


def test_add_edit_aborts_when_editor_fails(
    home: Path,
    # monkeypatch isolates process state (env, cwd, stdio): an external boundary
    # ast-grep-ignore: python.question-mocks
    monkeypatch: pytest.MonkeyPatch,
    *,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = make_repo(home)
    monkeypatch.setenv("EDITOR", failing_editor(3))

    assert main(add_args(repo, "--scope", "local", "--edit")) == 1

    assert "Editor exited with status 3" in capsys.readouterr().err


def test_add_rejects_invalid_rule_file(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = make_repo(home)
    source = home / "broken.yml"
    source.write_text("id: broken\n")

    assert main(add_args(repo, "--scope", "project", "--from", str(source))) == 1

    captured = capsys.readouterr()
    assert "missing required ast-grep fields" in captured.err
    assert "Traceback" not in captured.err
    assert not (repo / ".byor" / "rules" / "project" / "broken.yml").exists()


def test_add_rejects_duplicate_id_within_scope(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = make_repo(home)
    write_rule(repo / ".byor" / "rules" / "project" / "existing.yml", "no-cast")
    source = write_rule(home / "source.yml", "no-cast")

    assert main(add_args(repo, "--scope", "project", "--from", str(source))) == 1

    assert "Duplicate rule IDs" in capsys.readouterr().err
    assert not (repo / ".byor" / "rules" / "project" / "no-cast.yml").exists()


def test_add_refuses_to_overwrite_existing_destination(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = make_repo(home)
    source = write_rule(home / "source.yml", "no-cast")
    assert main(add_args(repo, "--scope", "project", "--from", str(source))) == 0

    assert main(add_args(repo, "--scope", "project", "--from", str(source))) == 1

    assert "already exists" in capsys.readouterr().err


def test_add_project_rule_overriding_a_global_id_is_allowed(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = make_repo(home)
    write_global_rule(home, "no-cast.yml", rule_id="no-cast")
    main(["sync", "--repo", str(repo)])
    source = write_rule(home / "source.yml", "no-cast", message="Stricter.")
    capsys.readouterr()

    assert main(add_args(repo, "--scope", "project", "--from", str(source))) == 0

    assert not (mirror(repo) / "no-cast.yml").exists()
    assert f"Synced 1 removed global rule into {repo}" in capsys.readouterr().out


def test_allow_exceptions_prefills_the_template(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = make_repo(home)
    capsys.readouterr()

    assert main(add_args(repo, "--scope", "project", "--allow-exceptions")) == 0

    assert ALLOW_EXCEPTIONS_SENTENCE in capsys.readouterr().out


def test_allow_exceptions_appends_to_an_existing_agent_prompt(home: Path) -> None:
    repo = make_repo(home)
    source = home / "source.yml"
    source.write_text(
        "id: no-cast\n"
        "language: Python\n"
        "message: Avoid this.\n"
        "rule:\n"
        "  pattern: cast($TYPE, $VALUE)\n"
        "metadata:\n"
        "  byor:\n"
        "    agent_prompt: Narrow the type instead.\n"
    )

    assert (
        main(
            add_args(
                repo,
                "--scope",
                "project",
                "--allow-exceptions",
                "--from",
                str(source),
            )
        )
        == 0
    )

    written = repo / ".byor" / "rules" / "project" / "no-cast.yml"
    assert written_agent_prompt(written) == (f"Narrow the type instead. {ALLOW_EXCEPTIONS_SENTENCE}")


def test_allow_exceptions_seeds_agent_prompt_from_message_when_absent(
    home: Path,
) -> None:
    repo = make_repo(home)
    source = write_rule(home / "source.yml", "no-cast")  # no metadata block

    assert (
        main(
            add_args(
                repo,
                "--scope",
                "project",
                "--allow-exceptions",
                "--from",
                str(source),
            )
        )
        == 0
    )

    written = repo / ".byor" / "rules" / "project" / "no-cast.yml"
    assert written_agent_prompt(written) == f"Avoid this. {ALLOW_EXCEPTIONS_SENTENCE}"


def test_allow_exceptions_with_edit_keeps_the_prefilled_sentence(
    home: Path,
    # monkeypatch isolates process state (env, cwd, stdio): an external boundary
    # ast-grep-ignore: python.question-mocks
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(home)
    monkeypatch.setenv("EDITOR", substituting_editor("REPLACE_ME", "no-cast"))

    assert main(add_args(repo, "--scope", "local", "--edit", "--allow-exceptions")) == 0

    destination = repo / ".byor" / "rules" / "personal" / "local" / "no-cast.yml"
    assert written_agent_prompt(destination).endswith(ALLOW_EXCEPTIONS_SENTENCE)


def test_add_warns_on_nonconforming_rule_id(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = make_repo(home)
    source = write_rule(home / "source.yml", "Bad_ID")

    assert main(add_args(repo, "--scope", "project", "--from", str(source))) == 0

    assert "does not match the recommended" in capsys.readouterr().err
    assert (repo / ".byor" / "rules" / "project" / "Bad_ID.yml").is_file()


def test_add_rejects_a_path_traversal_rule_id(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = make_repo(home)
    source = write_rule(home / "source.yml", "../../escaped")
    capsys.readouterr()

    assert main(add_args(repo, "--scope", "project", "--from", str(source))) == 1

    assert not (repo / ".byor" / "escaped.yml").exists()
    captured = capsys.readouterr()
    assert "escaped" in captured.err
    assert "Traceback" not in captured.err
