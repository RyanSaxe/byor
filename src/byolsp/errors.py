"""Expected error types for byolsp commands."""


class ByolspError(Exception):
    """Expected failure: rendered as a clean message, never a traceback."""

    exit_code: int = 1


class ConfigError(ByolspError):
    """A config file is malformed, has the wrong shape, or an unsupported version."""


class RepoNotInitialized(ByolspError):
    """The repository has no .byolsp/config.yml; `byolsp init` has not run here."""


class RuleValidationError(ByolspError):
    """A rule file is missing required ast-grep fields or has wrongly typed values."""


class DuplicateRuleId(ByolspError):
    """Two rule files share an ID in a way ast-grep would reject (SPEC section 14)."""
