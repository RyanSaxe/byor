"""Create or update sgconfig.yml so ast-grep sees the BYOR rule dirs."""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from ruamel.yaml.comments import CommentedMap

from byor.errors import ConfigError
from byor.yamlio import load_yaml_mapping, write_yaml_atomic

BACKUP_TIMESTAMP_FORMAT = "%Y%m%d-%H%M%S"


def ensure_rule_dirs(
    path: Path, rule_dirs: list[str], replace: bool = False
) -> str | None:
    """Make sure sgconfig.yml lists every rule dir, preserving user content.

    Missing entries are appended; existing keys, entries, and comments survive.
    `replace` rewrites the file from scratch after saving a timestamped backup.
    Returns a one-line summary of the change, or None when nothing changed.
    """
    if not path.is_file():
        write_yaml_atomic(path, _minimal_sgconfig(rule_dirs))
        return f"Created {path.name}"
    if replace:
        backup = _backup(path)
        write_yaml_atomic(path, _minimal_sgconfig(rule_dirs))
        return f"Replaced {path.name} (backup: {backup.name})"
    data = load_yaml_mapping(path)
    existing = data.get("ruleDirs")
    if existing is None:
        data["ruleDirs"] = list(rule_dirs)
        write_yaml_atomic(path, data)
        return f"Updated {path.name}"
    if not isinstance(existing, list):
        raise ConfigError(
            f"Cannot update {path.name}: expected ruleDirs to be a list.\n"
            f"Edit {path.name} manually or rerun with --replace-sgconfig."
        )
    missing = [rule_dir for rule_dir in rule_dirs if rule_dir not in existing]
    if not missing:
        return None
    existing.extend(missing)
    write_yaml_atomic(path, data)
    return f"Updated {path.name}"


def _minimal_sgconfig(rule_dirs: list[str]) -> CommentedMap:
    data = CommentedMap()
    data["ruleDirs"] = list(rule_dirs)
    return data


def _backup(path: Path) -> Path:
    stamp = datetime.now().strftime(BACKUP_TIMESTAMP_FORMAT)
    backup = path.with_name(f"{path.name}.byor-backup-{stamp}")
    shutil.copy2(path, backup)
    return backup
