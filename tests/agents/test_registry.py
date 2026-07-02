"""Assert the per-harness agent tables cover the same names.

Agent support is spread across uncoordinated tables: harness parsers and emitters, hook specs and
global directories, plugin agents, install choices, and manual steps. These completeness checks make
a new harness that misses one table fail in CI instead of at runtime.
"""

from byor.agents.harness import _EMITTERS, _PARSERS, HARNESS_CHOICES
from byor.agents.hookconfig import _GLOBAL_DIRS, HOOK_SPECS
from byor.agents.install import AGENT_CHOICES, HARNESS_MANUAL_STEPS, PLUGIN_AGENTS


def test_agent_choices_are_skill_plus_plugins_plus_harnesses() -> None:
    assert set(AGENT_CHOICES) == {"skill"} | set(PLUGIN_AGENTS) | set(HARNESS_CHOICES)


def test_every_harness_has_hook_config() -> None:
    assert set(HOOK_SPECS) == set(HARNESS_CHOICES)
    assert set(_GLOBAL_DIRS) == set(HARNESS_CHOICES)
    for name, spec in HOOK_SPECS.items():
        assert spec.harness == name


def test_every_harness_has_payload_parser_and_emitter() -> None:
    assert set(_PARSERS) == set(HARNESS_CHOICES)
    assert set(_EMITTERS) == set(HARNESS_CHOICES)


def test_manual_steps_only_name_known_harnesses() -> None:
    assert set(HARNESS_MANUAL_STEPS) <= set(HARNESS_CHOICES)
