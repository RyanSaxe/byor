"""Create sgconfig files for BYOR rule discovery.

ast-grep reads rule directories from sgconfig, so BYOR updates those files conservatively and backs
up replacements. The helpers preserve unrelated user content while making global and repository
rules visible to scans.
"""

from __future__ import annotations

import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

from ruamel.yaml.comments import CommentedMap

from byor.errors import ConfigError
from byor.io.paths import home_sgconfig_path
from byor.io.yamlio import load_yaml_mapping, write_yaml_atomic

__all__ = (
    "ensure_home_sgconfig",
    "ensure_rule_dirs",
    "remove_home_rule_dir",
)

BACKUP_TIMESTAMP_FORMAT = "%Y%m%d-%H%M%S"


def ensure_rule_dirs(path: Path, rule_dirs: list[str], *, replace: bool = False) -> str | None:
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
        msg = (
            f"Cannot update {path.name}: expected ruleDirs to be a list.\n"
            f"Edit {path.name} manually or rerun with --replace-sgconfig."
        )
        raise ConfigError(msg)
    missing = [rule_dir for rule_dir in rule_dirs if rule_dir not in existing]
    if not missing:
        return None
    existing.extend(missing)
    write_yaml_atomic(path, data)
    return f"Updated {path.name}"


def ensure_home_sgconfig(rules_dir: Path, home: Path | None = None) -> str | None:
    """Converge `~/sgconfig.yml` so ast-grep applies the global rules everywhere.

    Creates the rules directory and adds it to `ruleDirs` expressed relative to
    home (the sgconfig's own location). Appends to any pre-existing config the
    user already keeps and never clobbers it. Returns a one-line summary, or
    None when nothing changed.
    """
    home = home or Path.home()
    rules_dir.mkdir(parents=True, exist_ok=True)
    relpath = Path(os.path.relpath(rules_dir, home)).as_posix()
    return ensure_rule_dirs(home_sgconfig_path(home), [relpath])


def remove_home_rule_dir(rules_dir: Path, home: Path | None = None) -> bool:
    home = home or Path.home()
    path = home_sgconfig_path(home)
    if not path.is_file():
        return False
    relpath = Path(os.path.relpath(rules_dir, home)).as_posix()
    data = load_yaml_mapping(path)
    rule_dirs = data.get("ruleDirs")
    if not isinstance(rule_dirs, list) or relpath not in rule_dirs:
        return False
    rule_dirs.remove(relpath)
    if not rule_dirs and set(data) == {"ruleDirs"}:
        path.unlink()
    else:
        write_yaml_atomic(path, data)
    return True


def _minimal_sgconfig(rule_dirs: list[str]) -> CommentedMap:
    data = CommentedMap()
    data["ruleDirs"] = list(rule_dirs)
    return data


def _backup(path: Path) -> Path:
    stamp = datetime.now(tz=UTC).strftime(BACKUP_TIMESTAMP_FORMAT)
    backup = path.with_name(f"{path.name}.byor-backup-{stamp}")
    shutil.copy2(path, backup)
    return backup
