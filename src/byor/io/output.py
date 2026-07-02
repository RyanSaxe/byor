"""Write command output without using print.

Ruff's T201 rule is useful because library code should not grow casual stdout side effects. BYOR is
also a CLI, so intentional output is routed through this module to keep command behavior explicit
while satisfying that rule.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = (
    "write_line",
    "write_lines",
)


def write_line(line: str = "") -> None:
    message = f"{line}\n"
    sys.stdout.write(message)


def write_lines(lines: Iterable[str]) -> None:
    for line in lines:
        sys.stdout.write(f"{line}\n")
