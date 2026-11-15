"""Define BYOR exception types.

Commands raise these exceptions for expected user-facing failures rather than leaking tracebacks.
The small hierarchy also carries exit-code behavior, letting the CLI render consistent errors while
tests assert precise failure modes.
"""

__all__ = (
    "AstGrepNotFoundError",
    "ByorError",
    "ConfigError",
    "DuplicateRuleIdError",
    "RepoNotInitializedError",
    "RuleValidationError",
    "UnsafeOverwriteError",
    "WorktreeCopyBusyError",
)


class ByorError(Exception):
    exit_code: int = 1


class AstGrepNotFoundError(ByorError):
    pass


class ConfigError(ByorError):
    pass


class RepoNotInitializedError(ByorError):
    pass


class RuleValidationError(ByorError):
    pass


class DuplicateRuleIdError(ByorError):
    pass


class UnsafeOverwriteError(ByorError):
    pass


class WorktreeCopyBusyError(ByorError):
    """Another byor process holds the worktree copy lock past the wait.

    Commands stop on it rather than run against a half-copied worktree, because one that writes
    `.byor/local.yml` would mark the copy finished and lose the main checkout's settings; hooks
    report it and carry on, since a hook must never block an agent.
    """
