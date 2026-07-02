"""Exercise the BYOR command-line entry point.

Thin smoke coverage of the argparse surface: --help exits zero, --version prints the installed
package version and matches byor.__version__, and an unknown command exits nonzero. One parsing
detail is pinned because downstream behavior depends on it: agent-check --concise defaults to false
and the flag flips it.
"""

import importlib.metadata

import pytest

from byor import __version__
from byor.cli import build_parser, main


def test_help_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])

    assert excinfo.value.code == 0
    assert "byor" in capsys.readouterr().out


def test_version_prints_package_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])

    assert excinfo.value.code == 0
    assert capsys.readouterr().out.strip() == f"byor {__version__}"


def test_dunder_version_matches_installed_metadata() -> None:
    # __version__ is hardcoded in byor/__init__.py; this guards it against
    # drifting from the version pyproject.toml actually publishes.
    assert __version__ == importlib.metadata.version("byor")


def test_unknown_command_exits_nonzero() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["frobnicate"])

    assert excinfo.value.code != 0


def test_agent_check_concise_defaults_false_and_flag_sets_it() -> None:
    parser = build_parser()

    assert parser.parse_args(["agent-check"]).concise is False
    assert parser.parse_args(["agent-check", "--concise"]).concise is True
