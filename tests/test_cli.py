import pytest

from byolsp.cli import COMMANDS, main


def test_help_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])

    assert excinfo.value.code == 0
    assert "byolsp" in capsys.readouterr().out


def test_version_prints_package_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])

    assert excinfo.value.code == 0
    assert "byolsp 0.1" in capsys.readouterr().out


def test_unknown_command_exits_nonzero() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["frobnicate"])

    assert excinfo.value.code != 0


IMPLEMENTED = {"init", "sync", "doctor"}
INVOCATIONS = [
    [name] for name in sorted(COMMANDS) if name != "hook" and name not in IMPLEMENTED
] + [
    ["hook", "install"],
    ["hook", "uninstall"],
]


@pytest.mark.parametrize("argv", INVOCATIONS, ids=" ".join)
def test_command_is_registered_and_fails_cleanly(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(argv)

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "not implemented" in captured.err
    assert "Traceback" not in captured.err
