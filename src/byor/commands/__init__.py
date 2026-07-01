"""Group BYOR command implementations.

Each command module owns one user-facing workflow such as init, install, doctor, listing, packages,
or gates. The package itself exports nothing so callers go through the CLI dispatcher or targeted
modules.
"""

__all__ = ()
