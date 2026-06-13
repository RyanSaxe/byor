"""Expected error types for byor commands."""


class ByorError(Exception):
    """Expected failure: rendered as a clean message, never a traceback."""

    exit_code: int = 1


class AstGrepNotFound(ByorError):
    """No usable ast-grep executable could be resolved."""


class ConfigError(ByorError):
    """A config file is malformed, has the wrong shape, or an unsupported version."""


class RepoNotInitialized(ByorError):
    """The repository has no .byor/config.yml; `byor init` has not run here."""


class RuleValidationError(ByorError):
    """A rule file is invalid YAML or lacks the fields ast-grep requires."""


class DuplicateRuleId(ByorError):
    """Two rule files share an ID in a way ast-grep would reject."""


class UnsafeOverwrite(ByorError):
    """Refusing to overwrite an existing file without an explicit flag."""
