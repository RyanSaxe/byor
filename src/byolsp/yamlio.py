"""YAML file IO: comment-preserving reads, atomic writes."""

from __future__ import annotations

import io
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap
from ruamel.yaml.error import YAMLError
from ruamel.yaml.representer import SafeRepresenter

from byolsp.errors import ConfigError
from byolsp.fsio import write_text_atomic

# Wide enough that ruamel never rewraps long values like agent prompts.
YAML_LINE_WIDTH = 4096


def new_yaml() -> YAML:
    """A round-trip YAML processor configured for BYOLSP's output conventions."""
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = YAML_LINE_WIDTH
    yaml.indent(mapping=2, sequence=4, offset=2)
    # The round-trip default emits None as an empty scalar; spell it `null`.
    yaml.representer.add_representer(type(None), SafeRepresenter.represent_none)
    return yaml


def load_yaml_mapping(path: Path) -> CommentedMap:
    """Load a YAML file whose top level must be a mapping, preserving comments.

    Empty and comment-only documents load as an empty mapping.
    """
    try:
        data = new_yaml().load(path)
    except YAMLError as error:
        raise ConfigError(f"{path}: invalid YAML: {error}") from error
    if data is None:
        return CommentedMap()
    if not isinstance(data, CommentedMap):
        raise ConfigError(f"{path}: expected a YAML mapping at the top level")
    return data


def write_yaml_atomic(path: Path, data: CommentedMap) -> None:
    """Serialize a mapping and write it atomically."""
    stream = io.StringIO()
    new_yaml().dump(data, stream)
    write_text_atomic(path, stream.getvalue())
