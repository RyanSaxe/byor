"""Handle simple interactive command prompts.

BYOR keeps prompt formatting, choice rendering, and numeric selection parsing in one small module.
Commands can stay focused on setup decisions while this layer owns reusable terminal interaction
behavior.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from byor.io.output import write_line

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = (
    "ask",
    "numbers_to_choices",
    "print_options",
    "prompt_choice",
)


def prompt_choice(intro: str, options: Sequence[str], *, default: int = 0) -> int:
    print_options(intro, options)
    while True:
        raw = ask("Enter a number", default=str(default + 1))
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return int(raw) - 1
        write_line(f"Please enter a number between 1 and {len(options)}.")


def print_options(intro: str, options: Sequence[str]) -> None:
    write_line(intro)
    for number, option in enumerate(options, start=1):
        write_line(f"  {number}. {option}")


def ask(question: str, default: str) -> str:
    # input() echoes its prompt even when stdin is a pipe about to EOF, so a
    # non-TTY caller would see a question no one can answer; the prompt text
    # is withheld while a piped answer (or the EOF default) still applies.
    prompt = f"{question} [{default}]: " if sys.stdin.isatty() else ""
    try:
        answer = input(prompt).strip()
    except EOFError:
        return default
    return answer or default


def numbers_to_choices(raw: str, choices: Sequence[str]) -> list[str] | None:
    picks: list[str] = []
    for part in raw.split(","):
        number = part.strip()
        if not number.isdigit() or not 1 <= int(number) <= len(choices):
            return None
        pick = choices[int(number) - 1]
        if pick not in picks:
            picks.append(pick)
    return picks
