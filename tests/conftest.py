"""Shared scaffolding for CLI-level tests: an isolated home plus rule writers."""

from pathlib import Path

import pytest

from byolsp.cli import main

RULE_TEMPLATE = (
    "id: {rule_id}\n"
    "language: Python\n"
    "message: {message}\n"
    "rule:\n"
    "  pattern: cast($TYPE, $VALUE)\n"
)


@pytest.fixture
def home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """A sandbox holding repos and the global config dir (via XDG_CONFIG_HOME)."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    return tmp_path


def make_repo(home: Path, name: str = "repo", *extra: str) -> Path:
    repo = home / name
    repo.mkdir()
    assert main(["init", "--repo", str(repo), "--non-interactive", *extra]) == 0
    return repo


def write_rule(path: Path, rule_id: str, message: str = "Avoid this.") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(RULE_TEMPLATE.format(rule_id=rule_id, message=message))
    return path


def write_global_rule(home: Path, relpath: str, rule_id: str) -> Path:
    return write_rule(home / "xdg" / "byolsp" / "rules" / relpath, rule_id)
