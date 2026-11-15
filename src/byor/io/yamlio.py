"""Read and write BYOR YAML documents.

BYOR preserves comments and ordering in user-facing configuration, so YAML operations use ruamel
consistently. This module provides one parsing and dumping boundary for config, scaffold, and sync
code.
"""

from __future__ import annotations

import contextlib
import io
import json
from typing import TYPE_CHECKING

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap
from ruamel.yaml.error import YAMLError
from ruamel.yaml.representer import SafeRepresenter

from byor.errors import ConfigError
from byor.io.fsio import write_text_atomic

if TYPE_CHECKING:
    from pathlib import Path

__all__ = (
    "dump_yaml",
    "load_yaml_mapping",
    "new_yaml",
    "parse_yaml_mapping",
    "write_yaml_atomic",
    "yaml_scalar",
)

# Wide enough that ruamel never rewraps long values like agent prompts.
YAML_LINE_WIDTH = 4096


def new_yaml() -> YAML:
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = YAML_LINE_WIDTH
    yaml.indent(mapping=2, sequence=4, offset=2)
    # The round-trip default emits None as an empty scalar; spell it `null`.
    yaml.representer.add_representer(type(None), SafeRepresenter.represent_none)
    return yaml


def load_yaml_mapping(path: Path) -> CommentedMap:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        msg = f"{path}: not valid UTF-8: {error}"
        raise ConfigError(msg) from error
    except OSError as error:
        msg = f"{path}: cannot read: {error.strerror or error}"
        raise ConfigError(msg) from error
    return parse_yaml_mapping(text, source=path)


def parse_yaml_mapping(text: str, source: Path) -> CommentedMap:
    try:
        data = new_yaml().load(text)
    except YAMLError as error:
        msg = f"{source}: invalid YAML: {error}"
        raise ConfigError(msg) from error
    if data is None:
        return CommentedMap()
    if not isinstance(data, CommentedMap):
        msg = f"{source}: expected a YAML mapping at the top level"
        raise ConfigError(msg)
    return data


def dump_yaml(data: CommentedMap) -> str:
    stream = io.StringIO()
    new_yaml().dump(data, stream)
    return stream.getvalue()


def write_yaml_atomic(path: Path, data: CommentedMap) -> None:
    content = dump_yaml(data)
    write_text_atomic(path, content)


def yaml_scalar(value: str) -> str:
    """Spell `value` for a generated `key: value` line so YAML reads back the same string.

    A value that already reads back unchanged stays plain, so ordinary generated files keep their
    look. Anything YAML would parse differently, such as `true`, `a: b`, `x #y` or a leading
    quote, is double-quoted; JSON's string syntax is valid YAML for that.
    """
    with contextlib.suppress(YAMLError):
        if YAML(typ="safe").load(f"key: {value}") == {"key": value}:
            return value
    return json.dumps(value, ensure_ascii=False)
