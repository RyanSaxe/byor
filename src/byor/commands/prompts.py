"""Small interactive-prompt helpers shared by the init and install commands."""

from __future__ import annotations

from collections.abc import Sequence


def prompt_choice(intro: str, options: Sequence[str], default: int = 0) -> int:
    """Ask a numbered single-choice question; returns the zero-based index."""
    print_options(intro, options)
    while True:
        raw = ask("Enter a number", default=str(default + 1))
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return int(raw) - 1
        print(f"Please enter a number between 1 and {len(options)}.")


def print_options(intro: str, options: Sequence[str]) -> None:
    print(intro)
    for number, option in enumerate(options, start=1):
        print(f"  {number}. {option}")


def ask(question: str, default: str) -> str:
    try:
        answer = input(f"{question} [{default}]: ").strip()
    except EOFError:
        return default
    return answer or default


def numbers_to_choices(raw: str, choices: Sequence[str]) -> list[str] | None:
    """Parse a comma-separated list of 1-based option numbers into choices."""
    picks: list[str] = []
    for part in raw.split(","):
        number = part.strip()
        if not number.isdigit() or not 1 <= int(number) <= len(choices):
            return None
        pick = choices[int(number) - 1]
        if pick not in picks:
            picks.append(pick)
    return picks
