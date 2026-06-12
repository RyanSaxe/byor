"""YAML and text file IO: comment-preserving reads, atomic writes."""

from __future__ import annotations

import io
import os
import tempfile
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap
from ruamel.yaml.error import YAMLError
from ruamel.yaml.nodes import ScalarNode
from ruamel.yaml.representer import RoundTripRepresenter

from byolsp.errors import ConfigError

# Wide enough that ruamel never rewraps long values like agent prompts.
YAML_LINE_WIDTH = 4096


def _represent_none_as_null(
    representer: RoundTripRepresenter, value: None
) -> ScalarNode:
    return representer.represent_scalar("tag:yaml.org,2002:null", "null")


def new_yaml() -> YAML:
    """A round-trip YAML processor configured for BYOLSP's output conventions."""
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = YAML_LINE_WIDTH
    yaml.indent(mapping=2, sequence=4, offset=2)
    yaml.representer.add_representer(type(None), _represent_none_as_null)
    return yaml


def load_yaml_mapping(path: Path) -> CommentedMap:
    """Load a YAML file whose top level must be a mapping, preserving comments."""
    try:
        data = new_yaml().load(path)
    except YAMLError as error:
        raise ConfigError(f"{path}: invalid YAML: {error}") from error
    if not isinstance(data, CommentedMap):
        raise ConfigError(f"{path}: expected a YAML mapping at the top level")
    return data


def write_yaml_atomic(path: Path, data: CommentedMap) -> None:
    """Serialize a mapping and write it atomically."""
    stream = io.StringIO()
    new_yaml().dump(data, stream)
    write_text_atomic(path, stream.getvalue())


def write_text_atomic(path: Path, content: str) -> None:
    """Write via a temp file in the same directory, flush, then rename into place."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle_fd, temp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(handle_fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise
