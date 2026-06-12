"""Expected error types for byolsp commands."""


class ByolspError(Exception):
    """Expected failure: rendered as a clean message, never a traceback."""

    exit_code: int = 1
