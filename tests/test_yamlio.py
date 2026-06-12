from pathlib import Path

import pytest

from byolsp.errors import ConfigError
from byolsp.yamlio import load_yaml_mapping, write_yaml_atomic


def test_round_trip_preserves_comments_and_key_order(tmp_path: Path) -> None:
    path = tmp_path / "sgconfig.yml"
    path.write_text(
        "# team scanner config\n"
        "ruleDirs:\n"
        "  - rules  # existing entry\n"
        "utilDirs:\n"
        "  - utils\n"
    )

    data = load_yaml_mapping(path)
    data["testConfigs"] = ["tests"]
    write_yaml_atomic(path, data)

    content = path.read_text()
    assert "# team scanner config" in content
    assert "# existing entry" in content
    assert content.index("ruleDirs") < content.index("utilDirs")
    assert content.index("utilDirs") < content.index("testConfigs")


def test_load_treats_empty_document_as_empty_mapping(tmp_path: Path) -> None:
    path = tmp_path / "sgconfig.yml"
    path.write_text("# comment-only file\n")

    assert load_yaml_mapping(path) == {}


def test_load_rejects_non_mapping_top_level(tmp_path: Path) -> None:
    path = tmp_path / "list.yml"
    path.write_text("- just\n- a list\n")

    with pytest.raises(ConfigError, match="expected a YAML mapping"):
        load_yaml_mapping(path)


def test_load_rejects_invalid_yaml(tmp_path: Path) -> None:
    path = tmp_path / "broken.yml"
    path.write_text("key: [unclosed\n")

    with pytest.raises(ConfigError, match="invalid YAML"):
        load_yaml_mapping(path)
