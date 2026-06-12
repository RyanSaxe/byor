"""Rule-mutating commands: add, edit, promote, exclude, include (SPEC 15.4-15.7)."""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from byolsp.config import (
    RepoPaths,
    global_rules_dir,
    load_global_config,
    load_local_config,
    load_repo_config,
    save_local_config,
)
from byolsp.doctor import quick_doctor_problems
from byolsp.errors import (
    ByolspError,
    ConfigError,
    RuleValidationError,
    UnsafeOverwrite,
)
from byolsp.fsio import write_text_atomic
from byolsp.paths import global_config_dir, resolve_repo_root
from byolsp.rules import (
    Rule,
    RuleScope,
    check_id_conflicts,
    load_rule,
    load_rules,
    rule_id_warnings,
    scope_rules_dir,
)
from byolsp.sync import (
    SyncPlan,
    iter_registered_repos,
    load_canonical_rules,
    summarize_changes,
    sync_repo,
)

DEFAULT_EDITOR = "vi"

RULE_TEMPLATE = """\
id: {rule_id}
language: {language}
severity: warning
message: REPLACE_ME
rule:
  pattern: REPLACE_ME
metadata:
  byolsp:
    rationale: REPLACE_ME
    agent_prompt: REPLACE_ME
    allow_with_comment: false
    tags: []
"""


@dataclass
class RepoContext:
    """The resolved locations every rule command needs."""

    repo_root: Path
    config_dir: Path
    paths: RepoPaths
    global_rules_root: Path


def repo_context(args: argparse.Namespace) -> RepoContext:
    """Resolve the repo and global locations; fails on uninitialized repos."""
    repo_root = resolve_repo_root(explicit=args.repo)
    config_dir = global_config_dir()
    return RepoContext(
        repo_root=repo_root,
        config_dir=config_dir,
        paths=load_repo_config(repo_root).paths,
        global_rules_root=global_rules_dir(config_dir, load_global_config(config_dir)),
    )


def run_add(args: argparse.Namespace) -> int:
    template = _build_template(args.id, args.language)
    if args.from_file is None and not args.edit:
        print(template)
        print("Rerun with --from FILE or --edit to create the rule.")
        return 0
    context = repo_context(args)
    scope: RuleScope = args.scope
    draft: Path | None = None
    if args.from_file is not None:
        rule = _load_source_rule(args.from_file)
    else:
        draft = _edit_in_draft(template)
        if draft is None:
            raise ByolspError("Aborted: the template was left unedited.")
        rule = _parse_draft(draft)
    destination = _scope_dir(context, scope) / f"{rule.id}.yml"
    new_rule = replace(rule, path=destination)
    try:
        if destination.exists():
            raise UnsafeOverwrite(
                f"{_display(destination, context)} already exists; "
                f"use `byolsp edit {rule.id}` to change it."
            )
        _check_conflicts(context, scope, new_rule, replaced_path=None)
    except ByolspError as error:
        raise _with_draft_hint(error, draft) from error
    if draft is not None:
        draft.unlink()
    _warn_on_id_pattern(new_rule)
    write_text_atomic(destination, rule.content)
    print(f"Added {scope} rule '{rule.id}' at {_display(destination, context)}")
    _finish(context, fan_out=scope == "global")
    return 0


def run_edit(args: argparse.Namespace) -> int:
    context = repo_context(args)
    scope, path = _find_rule(context, args.rule_id, args.scope)
    original = path.read_text(encoding="utf-8")
    draft = _edit_in_draft(original)
    if draft is None:
        print(f"No changes to '{args.rule_id}'")
        return 0
    rule = replace(_parse_draft(draft), path=path)
    try:
        _check_conflicts(context, scope, rule, replaced_path=path)
    except ByolspError as error:
        raise _with_draft_hint(error, draft) from error
    draft.unlink()
    _warn_on_id_pattern(rule)
    write_text_atomic(path, rule.content)
    print(f"Updated {scope} rule '{rule.id}' at {_display(path, context)}")
    _finish(context, fan_out=scope == "global")
    return 0


def run_promote(args: argparse.Namespace) -> int:
    context = repo_context(args)
    source_scope: RuleScope = args.from_scope
    source_dir = _scope_dir(context, source_scope)
    rule = next(
        (r for r in load_rules(source_dir) if r.id == args.rule_id),
        None,
    )
    if rule is None:
        raise ByolspError(f"No {source_scope} rule with ID '{args.rule_id}'.")
    project_dir = context.repo_root / context.paths.project_rules
    destination = project_dir / rule.path.relative_to(source_dir)
    if destination.exists() and not args.replace:
        raise UnsafeOverwrite(
            f"{_display(destination, context)} already exists; "
            "rerun with --replace to overwrite it."
        )
    # SPEC 14 conflict check on the post-promote state, before any write. With
    # --keep-local this fails: keeping the local original would leave project
    # and local rules sharing the ID, which ast-grep rejects.
    remove_source = source_scope == "local" and not args.keep_local
    project = [r for r in load_rules(project_dir) if r.path != destination]
    project.append(replace(rule, path=destination))
    local = load_rules(context.repo_root / context.paths.personal_local_rules)
    if remove_source:
        local = [r for r in local if r.path != rule.path]
    check_id_conflicts(project, local, load_rules(context.global_rules_root))
    write_text_atomic(destination, rule.content)
    if remove_source:
        rule.path.unlink()
    print(f"Promoted '{rule.id}' to {_display(destination, context)}")
    _finish(context, fan_out=False)
    return 0


def run_exclude(args: argparse.Namespace) -> int:
    context = repo_context(args)
    local = load_local_config(context.repo_root)
    if args.rule_id in local.excluded_rule_ids:
        print(f"'{args.rule_id}' is already excluded")
    else:
        local.excluded_rule_ids.append(args.rule_id)
        save_local_config(context.repo_root, local)
        print(f"Excluded '{args.rule_id}' in .byolsp/local.yml")
    _sync_current_repo(context)
    return 0


def run_include(args: argparse.Namespace) -> int:
    context = repo_context(args)
    local = load_local_config(context.repo_root)
    if args.rule_id not in local.excluded_rule_ids:
        print(f"'{args.rule_id}' is not excluded")
    else:
        local.excluded_rule_ids.remove(args.rule_id)
        save_local_config(context.repo_root, local)
        print(f"Re-enabled '{args.rule_id}'")
    plan = _sync_current_repo(context)
    # A project or local rule may still own the ID (SPEC 15.7): say so.
    for rule_id, reason in plan.skipped:
        if rule_id == args.rule_id:
            print(f"'{rule_id}' is still skipped: {reason}")
    return 0


def _build_template(rule_id: str | None, language: str | None) -> str:
    return RULE_TEMPLATE.format(
        rule_id=rule_id or "REPLACE_ME", language=language or "Python"
    )


def _find_rule(
    context: RepoContext, rule_id: str, requested: RuleScope | Literal["auto"]
) -> tuple[RuleScope, Path]:
    """Resolve a rule ID to its scope and file (SPEC 15.5).

    `auto` tries project, then local, then canonical global. The global scope
    searches the canonical rules root via _scope_dir, so a generated copy
    under personal/global is never opened (SPEC 12.3).
    """
    if requested == "auto":
        scopes: tuple[RuleScope, ...] = ("project", "local", "global")
    else:
        scopes = (requested,)
    for scope in scopes:
        for rule in load_rules(_scope_dir(context, scope)):
            if rule.id == rule_id:
                return scope, rule.path
    where = "any scope" if requested == "auto" else f"{requested} rules"
    raise ByolspError(f"No rule with ID '{rule_id}' found in {where}.")


def _load_source_rule(source: Path) -> Rule:
    if not source.is_file():
        raise ConfigError(f"{source}: no such file")
    return load_rule(source)


def _edit_in_draft(content: str) -> Path | None:
    """Open `content` in $EDITOR via a draft file; None when left unchanged."""
    draft = _write_draft(content)
    try:
        _open_in_editor(draft)
    except ByolspError:
        draft.unlink(missing_ok=True)
        raise
    if draft.read_text(encoding="utf-8") == content:
        draft.unlink()
        return None
    return draft


def _write_draft(content: str) -> Path:
    handle, name = tempfile.mkstemp(prefix="byolsp-rule-", suffix=".yml")
    with os.fdopen(handle, "w", encoding="utf-8") as file:
        file.write(content)
    return Path(name)


def _open_in_editor(path: Path) -> None:
    """$EDITOR (shlex-split, default vi) as an argv list, never a shell string."""
    argv = [*shlex.split(os.environ.get("EDITOR") or DEFAULT_EDITOR), str(path)]
    result = subprocess.run(argv)
    if result.returncode != 0:
        raise ByolspError(f"Editor exited with status {result.returncode}; aborting.")


def _parse_draft(draft: Path) -> Rule:
    try:
        return load_rule(draft)
    except RuleValidationError as error:
        raise RuleValidationError(f"{error}\n{_draft_hint(draft)}") from error


def _with_draft_hint(error: ByolspError, draft: Path | None) -> ByolspError:
    """Point at the kept draft file so a failed add/edit never loses work."""
    if draft is None:
        return error
    return error.__class__(f"{error}\n{_draft_hint(draft)}")


def _draft_hint(draft: Path) -> str:
    return f"Your draft is saved at {draft}."


def _check_conflicts(
    context: RepoContext, scope: RuleScope, rule: Rule, replaced_path: Path | None
) -> None:
    """Enforce SPEC 14 for the rule set as it would be after writing `rule`."""
    scoped = {
        name: [
            existing
            for existing in load_rules(_scope_dir(context, name))
            if existing.path != replaced_path
        ]
        for name in ("project", "local", "global")
    }
    scoped[scope].append(rule)
    check_id_conflicts(scoped["project"], scoped["local"], scoped["global"])


def _warn_on_id_pattern(rule: Rule) -> None:
    for warning in rule_id_warnings([rule]):
        print(f"byolsp: warning: {warning}", file=sys.stderr)


def _scope_dir(context: RepoContext, scope: RuleScope) -> Path:
    return scope_rules_dir(
        scope, context.repo_root, context.paths, context.global_rules_root
    )


def _display(path: Path, context: RepoContext) -> str:
    """Repo-relative POSIX for paths inside the repo, absolute otherwise."""
    try:
        return path.relative_to(context.repo_root).as_posix()
    except ValueError:
        return str(path)


def _finish(context: RepoContext, fan_out: bool) -> None:
    """The shared post-action: sync, then surface `doctor --quick` problems.

    Global-scope mutations fan out to every registered repo (SPEC 3.2);
    everything else syncs the current repo only.
    """
    canonical = load_canonical_rules(context.config_dir)
    repos = [context.repo_root]
    if fan_out:
        repos.extend(
            repo
            for repo in iter_registered_repos(context.config_dir)
            if repo != context.repo_root
        )
    for repo in repos:
        _, result = sync_repo(repo, canonical)
        if result.changed:
            print(f"Synced {summarize_changes(result)} into {repo}")
    for problem in quick_doctor_problems(context.repo_root, context.config_dir):
        print(problem)


def _sync_current_repo(context: RepoContext) -> SyncPlan:
    """Post-action sync of the current repo, reporting only when it changed."""
    canonical = load_canonical_rules(context.config_dir)
    plan, result = sync_repo(context.repo_root, canonical)
    if result.changed:
        print(f"Synced {summarize_changes(result)} into {context.repo_root}")
    return plan
