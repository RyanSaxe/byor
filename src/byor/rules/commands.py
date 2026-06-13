"""Rule-mutating commands: add, edit, promote, exclude, include."""

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

from ruamel.yaml.comments import CommentedMap

from byor.commands.doctor import quick_doctor_problems
from byor.config import (
    RepoPaths,
    load_local_config,
    load_repo_config,
    save_local_config,
)
from byor.errors import ByorError, ConfigError, UnsafeOverwrite
from byor.io.fsio import write_text_atomic
from byor.io.paths import display_path, global_config_dir, resolve_repo_root
from byor.io.yamlio import dump_yaml, parse_yaml_mapping
from byor.rules.rules import (
    ALLOW_EXCEPTIONS_SENTENCE,
    Rule,
    RuleScope,
    check_id_conflicts,
    load_rule,
    load_rules,
    rule_id_warnings,
    scope_rules_dir,
)
from byor.rules.sync import (
    CanonicalRules,
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
  byor:
    rationale: REPLACE_ME
    agent_prompt: REPLACE_ME
    tags: []
"""


@dataclass
class RepoContext:
    """The resolved locations every rule command needs."""

    repo_root: Path
    config_dir: Path
    paths: RepoPaths
    canonical: CanonicalRules


def repo_context(args: argparse.Namespace) -> RepoContext:
    """Resolve the repo and global locations; fails on uninitialized repos."""
    repo_root = resolve_repo_root(explicit=args.repo)
    config_dir = global_config_dir()
    return RepoContext(
        repo_root=repo_root,
        config_dir=config_dir,
        paths=load_repo_config(repo_root).paths,
        canonical=load_canonical_rules(config_dir),
    )


def run_add(args: argparse.Namespace) -> int:
    template = _build_template(args.id, args.language)
    if args.allow_exceptions:
        template = _append_exception_sentence(template)
    if args.from_file is None and not args.edit:
        print(template)
        print("Rerun with --from FILE or --edit to create the rule.")
        return 0
    context = repo_context(args)
    scope: RuleScope = args.scope
    draft: Path | None = None
    if args.from_file is None:
        draft = _edit_in_draft(template)
        if draft is None:
            raise ByorError("Aborted: the template was left unedited.")
    try:
        if draft is not None:
            rule = load_rule(draft)
        else:
            rule = _load_source_rule(args.from_file)
            if args.allow_exceptions:
                rule = replace(rule, content=_append_exception_sentence(rule.content))
        destination = _scope_dir(context, scope) / f"{rule.id}.yml"
        if destination.exists():
            raise UnsafeOverwrite(
                f"{display_path(destination, context.repo_root)} already exists; "
                f"use `byor edit {rule.id}` to change it."
            )
        rule = replace(rule, path=destination)
        _check_conflicts(context, scope, rule, removed=set())
    except ByorError as error:
        raise _with_draft_hint(error, draft) from error
    if draft is not None:
        draft.unlink()
    _warn_on_id_pattern(rule)
    write_text_atomic(destination, rule.content)
    print(
        f"Added {scope} rule '{rule.id}' "
        f"at {display_path(destination, context.repo_root)}"
    )
    _finish(context, fan_out=scope == "global")
    return 0


def run_edit(args: argparse.Namespace) -> int:
    context = repo_context(args)
    scope, found = _find_rule(context, args.rule_id, args.scope)
    draft = _edit_in_draft(found.content)
    if draft is None:
        print(f"No changes to '{args.rule_id}'")
        return 0
    try:
        rule = replace(load_rule(draft), path=found.path)
        _check_conflicts(context, scope, rule, removed={found.path})
    except ByorError as error:
        raise _with_draft_hint(error, draft) from error
    draft.unlink()
    _warn_on_id_pattern(rule)
    write_text_atomic(found.path, rule.content)
    print(
        f"Updated {scope} rule '{rule.id}' "
        f"at {display_path(found.path, context.repo_root)}"
    )
    _finish(context, fan_out=scope == "global")
    return 0


def run_remove(args: argparse.Namespace) -> int:
    context = repo_context(args)
    scope, rule = _find_rule(context, args.rule_id, args.scope)
    rule.path.unlink()
    print(
        f"Removed {scope} rule '{rule.id}' "
        f"at {display_path(rule.path, context.repo_root)}"
    )
    _finish(context, fan_out=scope == "global")
    return 0


def run_promote(args: argparse.Namespace) -> int:
    context = repo_context(args)
    source_scope: RuleScope = args.from_scope
    _, rule = _find_rule(context, args.rule_id, source_scope)
    source_dir = _scope_dir(context, source_scope)
    project_dir = context.repo_root / context.paths.project_rules
    destination = project_dir / rule.path.relative_to(source_dir)
    if destination.exists() and not args.replace:
        raise UnsafeOverwrite(
            f"{display_path(destination, context.repo_root)} already exists; "
            "rerun with --replace to overwrite it."
        )
    # Conflict check on the post-promote state, before any write. With
    # --keep-local this fails: keeping the local original would leave project
    # and local rules sharing the ID, which ast-grep rejects.
    remove_source = source_scope == "local" and not args.keep_local
    removed = {destination, rule.path} if remove_source else {destination}
    _check_conflicts(context, "project", replace(rule, path=destination), removed)
    write_text_atomic(destination, rule.content)
    if remove_source:
        rule.path.unlink()
    print(f"Promoted '{rule.id}' to {display_path(destination, context.repo_root)}")
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
        print(f"Excluded '{args.rule_id}' in .byor/local.yml")
    _sync_and_report(context.repo_root, context.canonical)
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
    plan = _sync_and_report(context.repo_root, context.canonical)
    # A project or local rule may still own the ID: say so.
    for rule_id, reason in plan.skipped:
        if rule_id == args.rule_id:
            print(f"'{rule_id}' is still skipped: {reason}")
    return 0


def _build_template(rule_id: str | None, language: str | None) -> str:
    return RULE_TEMPLATE.format(
        rule_id=rule_id or "REPLACE_ME", language=language or "Python"
    )


def _append_exception_sentence(content: str) -> str:
    """Rule text whose metadata.byor.agent_prompt ends with the standard
    exception sentence, creating the metadata path when absent.

    A missing agent_prompt is seeded from `message` — the documented fallback —
    so the prompt still carries the fix instruction, not just the escape hatch.
    Callers pass already-validated rule YAML, so the parse error path never
    fires and the source name is a placeholder.
    """
    data = parse_yaml_mapping(content, source=Path("<rule>"))
    metadata = data.get("metadata")
    if not isinstance(metadata, CommentedMap):
        metadata = CommentedMap()
        data["metadata"] = metadata
    block = metadata.get("byor")
    if not isinstance(block, CommentedMap):
        block = CommentedMap()
        metadata["byor"] = block
    prompt = block.get("agent_prompt")
    if not isinstance(prompt, str):
        prompt = data.get("message")
    existing = prompt.strip() if isinstance(prompt, str) else ""
    block["agent_prompt"] = f"{existing} {ALLOW_EXCEPTIONS_SENTENCE}".lstrip()
    return dump_yaml(data)


def _find_rule(
    context: RepoContext, rule_id: str, requested: RuleScope | Literal["auto"]
) -> tuple[RuleScope, Rule]:
    """Resolve a rule ID to its scope and parsed rule.

    `auto` tries project, then local, then canonical global. The global scope
    searches the canonical rules root, so a generated copy under
    personal/global is never opened.
    """
    if requested == "auto":
        scopes: tuple[RuleScope, ...] = ("project", "local", "global")
    else:
        scopes = (requested,)
    for scope in scopes:
        for rule in _scope_rules(context, scope):
            if rule.id == rule_id:
                return scope, rule
    where = "any scope" if requested == "auto" else f"{requested} rules"
    raise ByorError(f"No rule with ID '{rule_id}' found in {where}.")


def _load_source_rule(source: Path) -> Rule:
    if not source.is_file():
        raise ConfigError(f"{source}: no such file")
    return load_rule(source)


def _edit_in_draft(content: str) -> Path | None:
    """Open `content` in $EDITOR via a draft file; None when left unchanged."""
    draft = _write_draft(content)
    try:
        _open_in_editor(draft)
    except ByorError:
        draft.unlink(missing_ok=True)
        raise
    if draft.read_text(encoding="utf-8") == content:
        draft.unlink()
        return None
    return draft


def _write_draft(content: str) -> Path:
    handle, name = tempfile.mkstemp(prefix="byor-rule-", suffix=".yml")
    with os.fdopen(handle, "w", encoding="utf-8") as file:
        file.write(content)
    return Path(name)


def _open_in_editor(path: Path) -> None:
    """$EDITOR (shlex-split, default vi) as an argv list, never a shell string."""
    argv = [*shlex.split(os.environ.get("EDITOR") or DEFAULT_EDITOR), str(path)]
    result = subprocess.run(argv)
    if result.returncode != 0:
        raise ByorError(f"Editor exited with status {result.returncode}; aborting.")


def _with_draft_hint(error: ByorError, draft: Path | None) -> ByorError:
    """Point at the kept draft file so a failed add/edit never loses work."""
    if draft is None:
        return error
    return type(error)(f"{error}\nYour draft is saved at {draft}.")


def _check_conflicts(
    context: RepoContext, scope: RuleScope, rule: Rule, removed: set[Path]
) -> None:
    """Enforce the conflict table for the rule set as it would be after writing `rule`.

    `removed` holds the file paths the command replaces or deletes, so their
    current contents do not count against the new rule.
    """
    scoped = {
        name: [
            existing
            for existing in _scope_rules(context, name)
            if existing.path not in removed
        ]
        for name in ("project", "local", "global")
    }
    scoped[scope].append(rule)
    check_id_conflicts(scoped["project"], scoped["local"], scoped["global"])


def _scope_rules(context: RepoContext, scope: RuleScope) -> list[Rule]:
    if scope == "global":
        return context.canonical.rules
    return load_rules(_scope_dir(context, scope))


def _warn_on_id_pattern(rule: Rule) -> None:
    for warning in rule_id_warnings([rule]):
        print(f"byor: warning: {warning}", file=sys.stderr)


def _scope_dir(context: RepoContext, scope: RuleScope) -> Path:
    return scope_rules_dir(
        scope, context.repo_root, context.paths, context.canonical.root
    )


def _finish(context: RepoContext, fan_out: bool) -> None:
    """The shared post-action: sync, then surface `doctor --quick` problems.

    Global-scope mutations fan out to every registered repo and
    reload the canonical rules the mutation just changed; everything else
    syncs the current repo with the rules already in hand.
    """
    canonical = (
        load_canonical_rules(context.config_dir) if fan_out else context.canonical
    )
    repos = [context.repo_root]
    if fan_out:
        repos.extend(
            repo
            for repo in iter_registered_repos(context.config_dir)
            if repo != context.repo_root
        )
    for repo in repos:
        _sync_and_report(repo, canonical)
    for problem in quick_doctor_problems(context.repo_root, context.config_dir):
        print(problem)


def _sync_and_report(repo_root: Path, canonical: CanonicalRules) -> SyncPlan:
    """Post-action sync of one repo, reporting only when it changed."""
    plan, result = sync_repo(repo_root, canonical)
    if result.changed:
        print(f"Synced {summarize_changes(result)} into {repo_root}")
    return plan
