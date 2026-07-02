"""`byor init --gate`: distribute a byor-free blocking gate to the team.

The gate must run for contributors who never installed byor, so generation vendors everything the
pre-commit config needs into the repo — including home-directory check scripts, whose references get
rewritten and whose name collisions are refused. It also self-heals when checks are added later,
keeps out of an existing pre-commit config, and supports a private variant that installs a local
shim and commits nothing.
"""

from pathlib import Path

import pytest
from support import git, install_package, write_global_check, write_global_rule, write_package_rule, write_rule

from byor.cli import main
from byor.config import CheckDef, load_repo_config, save_repo_config
from byor.scaffold.precommit import AST_GREP_CLI_VERSION


def gate_repo(home: Path, *, extra: tuple[str, ...] = (), branch: str = "main") -> Path:
    repo = home / "repo"
    repo.mkdir()
    git(repo, "init", "--quiet", f"--initial-branch={branch}")
    args = ["init", "--repo", str(repo), "--non-interactive", "--gate", *extra]
    assert main(args) == 0
    return repo


def test_gate_promotes_rules_and_checks_and_writes_portable_artifacts(
    home: Path,
) -> None:
    write_global_rule(home, "python/no-cast.yml", rule_id="no-cast")
    write_global_check("ruff", "ruff-check")
    repo = gate_repo(home)

    config = load_repo_config(repo)
    assert (repo / ".byor" / "rules" / "project" / "python" / "no-cast.yml").is_file()
    assert [check.name for check in config.checks] == ["ruff"]
    assert config.gate is True

    precommit = (repo / ".pre-commit-config.yaml").read_text()
    # The gate pins ast-grep so enforcement never drifts with upstream releases.
    assert f"uvx --from ast-grep-cli=={AST_GREP_CLI_VERSION} ast-grep scan --error" in precommit
    assert "ruff-check" in precommit
    # A check's extensions become a pre-commit files filter (byor-faithful scoping).
    assert r"files: \.(py)$" in precommit

    workflow = (repo / ".github" / "workflows" / "byor-gate.yml").read_text()
    # Push runs are limited to the default branch so PR branches are not gated twice.
    assert "on:\n  pull_request:\n  push:\n    branches: [main]\n" in workflow
    assert "astral-sh/setup-uv@v6" in workflow
    assert f"uvx --from ast-grep-cli=={AST_GREP_CLI_VERSION} ast-grep scan --error" in workflow
    assert "npm install" not in workflow
    assert "ruff-check" in workflow


def test_gate_prints_the_pre_commit_install_hint_until_a_hook_exists(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Nothing installs the framework hook for the user: without this hint the
    # gate looks green while enforcing nothing on local commits.
    repo = gate_repo(home)

    assert "run `uvx pre-commit install`" in capsys.readouterr().out

    # Any pre-commit hook file counts as active; its internals are not inspected.
    (repo / ".git" / "hooks" / "pre-commit").write_text("#!/bin/sh\nexec pre-commit run\n")
    assert main(["init", "--repo", str(repo), "--non-interactive", "--gate"]) == 0
    assert "uvx pre-commit install" not in capsys.readouterr().out


def test_gate_workflow_gates_pushes_to_a_non_main_default_branch(home: Path) -> None:
    repo = gate_repo(home, branch="trunk")

    workflow = (repo / ".github" / "workflows" / "byor-gate.yml").read_text()
    assert "on:\n  pull_request:\n  push:\n    branches: [trunk]\n" in workflow


def test_gate_workflow_branch_is_stable_across_checkouts(home: Path) -> None:
    """Without origin/HEAD the branch used to be re-detected on every heal.

    `git checkout -b feature` plus any byor command silently rewrote the
    committed workflow to gate pushes to `feature`; the branch recorded at
    install time keeps regeneration (and doctor's staleness view) stable.
    """
    repo = gate_repo(home)
    workflow = repo / ".github" / "workflows" / "byor-gate.yml"
    assert load_repo_config(repo).gate_branch == "main"

    git(repo, "commit", "--allow-empty", "-q", "-m", "init")
    git(repo, "checkout", "-q", "-b", "feature")
    assert main(["list", "--repo", str(repo)]) == 0

    assert "branches: [main]" in workflow.read_text()
    assert main(["doctor", "--repo", str(repo)]) == 0


# Doctor's drift remediation says "run `byor init --gate`", which users run
# from whatever branch they are on; re-recording gate_branch there resurrected
# the CI branch flap the recorded value exists to prevent.
def test_rerunning_init_gate_keeps_the_recorded_branch(home: Path) -> None:
    repo = gate_repo(home)
    git(repo, "commit", "--allow-empty", "-q", "-m", "init")
    git(repo, "checkout", "-q", "-b", "feature")

    assert main(["init", "--repo", str(repo), "--non-interactive", "--gate"]) == 0

    assert load_repo_config(repo).gate_branch == "main"
    workflow = (repo / ".github" / "workflows" / "byor-gate.yml").read_text()
    assert "branches: [main]" in workflow


def test_gate_without_a_recorded_branch_falls_back_to_detection(home: Path) -> None:
    repo = gate_repo(home, branch="trunk")
    config = load_repo_config(repo)
    config.gate_branch = None
    save_repo_config(repo, config)  # a config written before byor recorded the branch
    (repo / ".github" / "workflows" / "byor-gate.yml").unlink()

    assert main(["list", "--repo", str(repo)]) == 0

    workflow = (repo / ".github" / "workflows" / "byor-gate.yml").read_text()
    assert "branches: [trunk]" in workflow


def test_gate_fail_on_error_drops_the_error_flag_from_both_files(home: Path) -> None:
    repo = gate_repo(home)
    config = load_repo_config(repo)
    config.fail_on = "error"
    save_repo_config(repo, config)

    # Any subsequent repo command regenerates the byor-owned artifacts.
    assert main(["list", "--repo", str(repo)]) == 0

    precommit = (repo / ".pre-commit-config.yaml").read_text()
    workflow = (repo / ".github" / "workflows" / "byor-gate.yml").read_text()
    assert f"entry: uvx --from ast-grep-cli=={AST_GREP_CLI_VERSION} ast-grep scan\n" in precommit
    assert f"- run: uvx --from ast-grep-cli=={AST_GREP_CLI_VERSION} ast-grep scan\n" in workflow
    assert "--error" not in precommit
    assert "--error" not in workflow
    # Doctor's staleness view renders from the same setting.
    assert main(["doctor", "--repo", str(repo)]) == 0


def test_gate_self_heals_when_a_check_is_added_later(home: Path) -> None:
    write_global_rule(home, "python/no-cast.yml", rule_id="no-cast")
    repo = gate_repo(home)
    assert "later-check" not in (repo / ".pre-commit-config.yaml").read_text()

    write_global_check("later", "later-check")
    assert main(["promote", "--repo", str(repo), "--check", "later"]) == 0
    # Any subsequent repo command regenerates the byor-owned artifacts.
    assert main(["list", "--repo", str(repo)]) == 0

    assert "later-check" in (repo / ".pre-commit-config.yaml").read_text()
    assert "later-check" in (repo / ".github" / "workflows" / "byor-gate.yml").read_text()


# monkeypatch isolates process state (env, cwd, stdio): an external boundary
# ast-grep-ignore: python.question-mocks
def test_gate_vendors_a_home_script_check_into_the_repo(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Redirect `~` into the sandbox on both POSIX (HOME) and Windows (USERPROFILE),
    # since os.path.expanduser reads different vars per platform.
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    (home / "fix.sh").write_text("#!/bin/sh\necho hi\n")
    write_global_check("fixer", "~/fix.sh")
    repo = gate_repo(home)

    assert (repo / ".byor" / "scripts" / "fix.sh").is_file()
    run = next(c.run for c in load_repo_config(repo).checks if c.name == "fixer")
    assert run == ".byor/scripts/fix.sh"


# monkeypatch isolates process state (env, cwd, stdio): an external boundary
# ast-grep-ignore: python.question-mocks
def test_gate_rewrites_vendored_home_script_dependencies(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    scripts = home / ".config" / "byor" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "runner.sh").write_text('#!/usr/bin/env zsh\nexec "${HOME}/.config/byor/scripts/helper.py" "$@"\n')
    (scripts / "helper.py").write_text("#!/usr/bin/env python3\nprint('ok')\n")
    write_global_check("runner", "~/.config/byor/scripts/runner.sh")

    repo = gate_repo(home)

    runner = repo / ".byor" / "scripts" / "runner.sh"
    helper = repo / ".byor" / "scripts" / "helper.py"
    assert runner.is_file()
    assert helper.is_file()
    assert runner.read_text() == (
        "#!/usr/bin/env zsh\n"
        "# Vendored by BYOR from ~/.config/byor/scripts/runner.sh. "
        "Managed by BYOR. Manual edits may be overwritten.\n"
        'exec ".byor/scripts/helper.py" "$@"\n'
    )


# monkeypatch isolates process state (env, cwd, stdio): an external boundary
# ast-grep-ignore: python.question-mocks
def test_gate_vendors_subdirectory_script_references(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    scripts = home / ".config" / "byor" / "scripts"
    (scripts / "sub").mkdir(parents=True)
    (scripts / "runner.sh").write_text('#!/usr/bin/env zsh\nexec "${HOME}/.config/byor/scripts/sub/tool.py" "$@"\n')
    (scripts / "sub" / "tool.py").write_text("#!/usr/bin/env python3\nprint('ok')\n")
    write_global_check("runner", "~/.config/byor/scripts/runner.sh")

    repo = gate_repo(home)

    runner = repo / ".byor" / "scripts" / "runner.sh"
    assert (repo / ".byor" / "scripts" / "sub" / "tool.py").is_file()
    assert runner.read_text() == (
        "#!/usr/bin/env zsh\n"
        "# Vendored by BYOR from ~/.config/byor/scripts/runner.sh. "
        "Managed by BYOR. Manual edits may be overwritten.\n"
        'exec ".byor/scripts/sub/tool.py" "$@"\n'
    )


# Deleting the now-redundant home helper used to flag false drift, and the
# prescribed re-vendor rewrote the committed copy back to a `~/...` path.
def test_gate_keeps_a_rewritten_reference_when_its_home_source_disappears(
    home: Path,
    # monkeypatch isolates process state (env, cwd, stdio): an external boundary
    # ast-grep-ignore: python.question-mocks
    monkeypatch: pytest.MonkeyPatch,
    *,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    scripts = home / ".config" / "byor" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "runner.sh").write_text('#!/usr/bin/env zsh\nexec "${HOME}/.config/byor/scripts/helper.py" "$@"\n')
    (scripts / "helper.py").write_text("#!/usr/bin/env python3\nprint('ok')\n")
    write_global_check("runner", "~/.config/byor/scripts/runner.sh")
    repo = gate_repo(home)
    (scripts / "helper.py").unlink()  # redundant now that the repo carries a copy
    capsys.readouterr()

    assert main(["doctor", "--repo", str(repo)]) == 0
    assert main(["list", "--repo", str(repo)]) == 0  # self-heal must not corrupt

    runner = repo / ".byor" / "scripts" / "runner.sh"
    assert 'exec ".byor/scripts/helper.py" "$@"' in runner.read_text()
    assert "Re-vendored" not in capsys.readouterr().err


# A repo-origin `~/...` check used to reach the committed gate verbatim,
# passing locally while failing on every teammate machine and CI runner.
# monkeypatch isolates process state (env, cwd, stdio): an external boundary
# ast-grep-ignore: python.question-mocks
def test_gate_vendors_home_scripts_referenced_by_repo_checks(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    (home / "fix.sh").write_text("#!/bin/sh\necho hi\n")
    repo = home / "repo"
    repo.mkdir()
    git(repo, "init", "--quiet", "--initial-branch=main")
    assert main(["init", "--repo", str(repo), "--non-interactive"]) == 0
    config = load_repo_config(repo)
    config.checks.append(CheckDef("fixer", ["py"], "~/fix.sh"))
    save_repo_config(repo, config)

    assert main(["init", "--repo", str(repo), "--non-interactive", "--gate"]) == 0

    run = next(c.run for c in load_repo_config(repo).checks if c.name == "fixer")
    assert run == ".byor/scripts/fix.sh"
    workflow = (repo / ".github" / "workflows" / "byor-gate.yml").read_text()
    assert ".byor/scripts/fix.sh" in workflow
    assert "~/fix.sh" not in workflow


# The collision fallback only covered packages: a global rule whose filename
# matched a project rule was silently dropped from the gate.
def test_gate_promote_keeps_a_global_rule_colliding_with_a_project_filename(home: Path) -> None:
    write_global_rule(home, "python/no-cast.yml", rule_id="global-no-cast")
    repo = home / "repo"
    repo.mkdir()
    git(repo, "init", "--quiet")
    assert main(["init", "--repo", str(repo), "--non-interactive"]) == 0
    project = repo / ".byor" / "rules" / "project"
    write_rule(project / "python" / "no-cast.yml", "proj-no-cast")

    assert main(["init", "--repo", str(repo), "--non-interactive", "--gate"]) == 0
    assert main(["init", "--repo", str(repo), "--non-interactive", "--gate"]) == 0

    ids = sorted(
        line for path in project.rglob("*.yml") for line in path.read_text().splitlines() if line.startswith("id: ")
    )
    assert ids == ["id: global-no-cast", "id: proj-no-cast"]


# Case-insensitive filesystems merge Lint.py and lint.py into one file, so
# the collision must be refused even where the checkout is case-sensitive.
def test_gate_refuses_two_scripts_vendoring_to_case_variant_names(
    home: Path,
    # monkeypatch isolates process state (env, cwd, stdio): an external boundary
    # ast-grep-ignore: python.question-mocks
    monkeypatch: pytest.MonkeyPatch,
    *,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    (home / "toolsA").mkdir()
    (home / "toolsB").mkdir()
    (home / "toolsA" / "Lint.py").write_text("print('upper')\n")
    (home / "toolsB" / "lint.py").write_text("print('lower')\n")
    write_global_check("check-upper", "~/toolsA/Lint.py")
    write_global_check("check-lower", "~/toolsB/lint.py")
    repo = home / "repo"
    repo.mkdir()
    git(repo, "init", "--quiet")
    capsys.readouterr()

    assert main(["init", "--repo", str(repo), "--non-interactive", "--gate"]) == 1

    assert "rename one of the scripts" in capsys.readouterr().err


# A gate repo whose only extra check runs a vendored copy of ~/fix.sh.
def script_check_repo(
    home: Path,
    # monkeypatch isolates process state (env, cwd, stdio): an external boundary
    # ast-grep-ignore: python.question-mocks
    monkeypatch: pytest.MonkeyPatch,
    *,
    content: str = "#!/bin/sh\necho one\n",
) -> Path:
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    (home / "fix.sh").write_text(content)
    write_global_check("fixer", "~/fix.sh")
    return gate_repo(home)


# monkeypatch isolates process state (env, cwd, stdio): an external boundary
# ast-grep-ignore: python.question-mocks
def test_gate_revendors_a_script_when_its_source_changes(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = script_check_repo(home, monkeypatch)
    vendored = repo / ".byor" / "scripts" / "fix.sh"
    assert "Vendored by BYOR from ~/fix.sh" in vendored.read_text()
    assert "echo one" in vendored.read_text()

    (home / "fix.sh").write_text("#!/bin/sh\necho two\n")
    assert main(["list", "--repo", str(repo)]) == 0  # any command self-heals the gate

    text = vendored.read_text()
    assert "echo two" in text
    assert "Vendored by BYOR from ~/fix.sh" in text


def test_gate_leaves_a_vendored_script_alone_when_its_source_is_missing(
    home: Path,
    # monkeypatch isolates process state (env, cwd, stdio): an external boundary
    # ast-grep-ignore: python.question-mocks
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = script_check_repo(home, monkeypatch)
    vendored = repo / ".byor" / "scripts" / "fix.sh"
    (home / "fix.sh").unlink()  # a teammate's machine never has the source

    assert main(["list", "--repo", str(repo)]) == 0

    assert "echo one" in vendored.read_text()


def test_gate_never_rewrites_a_vendored_script_whose_marker_was_removed(
    home: Path,
    # monkeypatch isolates process state (env, cwd, stdio): an external boundary
    # ast-grep-ignore: python.question-mocks
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = script_check_repo(home, monkeypatch)
    vendored = repo / ".byor" / "scripts" / "fix.sh"
    owned = "#!/bin/sh\necho mine\n"
    vendored.write_text(owned)  # stripping the marker takes ownership
    (home / "fix.sh").write_text("#!/bin/sh\necho theirs\n")

    assert main(["list", "--repo", str(repo)]) == 0
    assert main(["init", "--repo", str(repo), "--non-interactive", "--gate"]) == 0

    assert vendored.read_text() == owned


def test_gate_refuses_two_scripts_vendoring_to_the_same_name(
    home: Path,
    # monkeypatch isolates process state (env, cwd, stdio): an external boundary
    # ast-grep-ignore: python.question-mocks
    monkeypatch: pytest.MonkeyPatch,
    *,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    (home / "fix.sh").write_text("#!/bin/sh\necho direct\n")
    scripts = home / ".config" / "byor" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "runner.sh").write_text('#!/bin/sh\nexec "${HOME}/.config/byor/scripts/fix.sh"\n')
    (scripts / "fix.sh").write_text("#!/bin/sh\necho nested\n")
    write_global_check("fixer", "~/fix.sh")
    write_global_check("runner", "~/.config/byor/scripts/runner.sh")
    repo = home / "repo"
    repo.mkdir()
    git(repo, "init", "--quiet")
    capsys.readouterr()

    assert main(["init", "--repo", str(repo), "--non-interactive", "--gate"]) == 1

    assert "rename one of the scripts" in capsys.readouterr().err


def test_gate_promote_keeps_both_package_rules_on_a_filename_collision(home: Path) -> None:
    """Two packages shipping the same filename (distinct IDs) must both be vendored.

    The loser used to be dropped silently, so the committed gate never
    enforced it on CI or fresh clones; the collision now lands under a
    package-prefixed path, and regenerating stays idempotent.
    """
    write_package_rule(home, "pkg-a", relpath="no-cast.yml", rule_id="a-no-cast")
    write_package_rule(home, "pkg-b", relpath="no-cast.yml", rule_id="b-no-cast")
    repo = home / "repo"
    repo.mkdir()
    git(repo, "init", "--quiet")
    install_package(repo, "pkg-a")
    install_package(repo, "pkg-b")

    assert main(["init", "--repo", str(repo), "--non-interactive", "--gate"]) == 0
    assert main(["init", "--repo", str(repo), "--non-interactive", "--gate"]) == 0

    project = repo / ".byor" / "rules" / "project"
    vendored = sorted(project.rglob("*.yml"))
    ids = sorted(line for path in vendored for line in path.read_text().splitlines() if line.startswith("id: "))
    assert ids == ["id: a-no-cast", "id: b-no-cast"]
    # Both copies live in the committed project dir, visible to git.
    visible = git(repo, "ls-files", "--others", "--exclude-standard", "--", str(project))
    assert visible.count("no-cast.yml") == 2


def test_gate_promote_message_pluralizes_each_noun(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write_global_rule(home, "no-cast.yml", rule_id="no-cast")
    write_global_check("ruff", "ruff-check")
    capsys.readouterr()

    gate_repo(home)

    assert "Promoted 1 rule and 1 check into tracked config" in capsys.readouterr().out


def test_private_gate_installs_a_local_shim_and_commits_nothing(home: Path) -> None:
    write_global_rule(home, "python/no-cast.yml", rule_id="no-cast")
    repo = gate_repo(home, extra=("--private",))

    assert (repo / ".git" / "hooks" / "pre-commit").is_file()
    assert not (repo / ".pre-commit-config.yaml").exists()
    assert not (repo / ".github").exists()
    status = git(repo, "status", "--porcelain")
    assert ".byor" not in status
    assert "sgconfig" not in status


def test_gate_does_not_clobber_an_existing_precommit_config(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write_global_rule(home, "python/no-cast.yml", rule_id="no-cast")
    repo = home / "repo"
    repo.mkdir()
    git(repo, "init", "--quiet")
    (repo / ".pre-commit-config.yaml").write_text("repos: []\n")
    capsys.readouterr()

    assert main(["init", "--repo", str(repo), "--non-interactive", "--gate"]) == 0

    assert (repo / ".pre-commit-config.yaml").read_text() == "repos: []\n"
    assert "already exists" in capsys.readouterr().out


# The pre-commit writer already printed adoption guidance; the CI writer used
# to no-op silently, leaving the user with gate: true and no CI enforcement.
def test_gate_does_not_clobber_an_existing_ci_workflow_but_prints_guidance(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_global_rule(home, "python/no-cast.yml", rule_id="no-cast")
    repo = home / "repo"
    repo.mkdir()
    git(repo, "init", "--quiet")
    workflow = repo / ".github" / "workflows" / "byor-gate.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text("name: my own gate\n")
    capsys.readouterr()

    assert main(["init", "--repo", str(repo), "--non-interactive", "--gate"]) == 0

    assert workflow.read_text() == "name: my own gate\n"
    out = capsys.readouterr().out
    assert ".github/workflows/byor-gate.yml already exists" in out
    assert "ast-grep scan" in out
