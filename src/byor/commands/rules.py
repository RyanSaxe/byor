"""Implement BYOR rule mutation commands.

Add, edit, remove, promote, include, and exclude all modify rule or check state and then resync the
repository. This module keeps those workflows together so conflict checks, draft handling, and fan-
out behavior stay consistent.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from ruamel.yaml.comments import CommentedMap

from byor.commands.doctor import quick_doctor_problems
from byor.config import (
    CommandRuleScope,
    LocalConfig,
    RepoPaths,
    command_rules_relpath,
    load_global_config,
    load_local_config,
    load_repo_config,
    repo_config_path,
    save_local_config,
    save_repo_config,
)
from byor.errors import (
    AstGrepNotFoundError,
    ByorError,
    ConfigError,
    RepoNotInitializedError,
    RuleValidationError,
    UnsafeOverwriteError,
)
from byor.io.fsio import write_text_atomic
from byor.io.output import write_line
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
    load_installed_packages,
    summarize_changes,
    sync_repo,
)
from byor.scan.astgrep import resolve_ast_grep, rule_load_error
from byor.scan.checks import load_effective_checks

if TYPE_CHECKING:
    import argparse
    from collections.abc import Callable

__all__ = (
    "ExclusionKind",
    "ExclusionSelector",
    "RepoContext",
    "repo_context",
    "run_add",
    "run_edit",
    "run_exclusion",
    "run_promote",
    "run_remove",
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
    repo_root: Path
    config_dir: Path
    paths: RepoPaths
    canonical: CanonicalRules


def repo_context(args: argparse.Namespace) -> RepoContext:
    repo_root = resolve_repo_root(explicit=args.repo)
    config_dir = global_config_dir()
    return RepoContext(
        repo_root=repo_root,
        config_dir=config_dir,
        paths=load_repo_config(repo_root).paths,
        canonical=load_canonical_rules(config_dir),
    )


def _rule_context(args: argparse.Namespace, *, scope: str) -> RepoContext:
    """Build the context, tolerating a missing repo for global-scope work.

    Global rules live in the user's config dir and work everywhere without
    `byor init`, so global (and auto, which can only find global rules here)
    lookups must not demand one. The default paths then point at directories
    that do not exist, so the project and local scopes simply read as empty.
    """
    try:
        return repo_context(args)
    except RepoNotInitializedError:
        if scope not in ("global", "auto"):
            raise
        config_dir = global_config_dir()
        return RepoContext(
            repo_root=resolve_repo_root(explicit=args.repo),
            config_dir=config_dir,
            paths=RepoPaths(),
            canonical=load_canonical_rules(config_dir),
        )


def run_add(args: argparse.Namespace) -> int:
    command: bool = args.command_rule
    template = _add_template(args)
    if args.from_file is None and not args.edit:
        write_line(template.rstrip())
        write_line("Rerun with --from FILE or --edit to create the rule.")
        return 0
    context = _rule_context(args, scope=args.scope)
    scope: RuleScope = args.scope
    draft: Path | None = None
    if args.from_file is None:
        draft = _edit_in_draft(template)
        if draft is None:
            msg = "Aborted: the template was left unedited."
            raise ByorError(msg)
    try:
        if draft is not None:
            rule = load_rule(draft)
        else:
            rule = _load_source_rule(args.from_file)
            if args.allow_exceptions:
                rule = replace(rule, content=_append_exception_sentence(rule.content))
        destination = _scope_dir(context, scope, command=command) / f"{rule.id}.yml"
        if destination.exists():
            msg = (
                f"{display_path(destination, context.repo_root)} already exists; "
                f"use `byor edit {rule.id}` to change it."
            )
            raise UnsafeOverwriteError(msg)
        rule = replace(rule, path=destination)
        _check_conflicts(context, scope, rule=rule, removed=set(), command=command)
        _require_loadable_by_ast_grep(context, rule)
    except ByorError as error:
        raise _with_draft_hint(error, draft) from error
    if draft is not None:
        draft.unlink()
    _warn_on_id_pattern(rule)
    _warn_on_command_language(rule, command=command)
    write_text_atomic(destination, rule.content)
    kind = "command rule" if command else "rule"
    write_line(f"Added {scope} {kind} '{rule.id}' at {display_path(destination, context.repo_root)}")
    _finish(context, fan_out=scope == "global")
    return 0


def _add_template(args: argparse.Namespace) -> str:
    default_language = "Bash" if args.command_rule else "Python"
    template = RULE_TEMPLATE.format(rule_id=args.id or "REPLACE_ME", language=args.language or default_language)
    return _append_exception_sentence(template) if args.allow_exceptions else template


def _warn_on_command_language(rule: Rule, *, command: bool) -> None:
    if command and rule.language.lower() != "bash":
        sys.stderr.write(
            f"byor: warning: the pre-command gate parses commands as Bash and will skip"
            f" this {rule.language} rule; use `language: Bash`\n"
        )


def run_edit(args: argparse.Namespace) -> int:
    context = _rule_context(args, scope=args.scope)
    scope, found = _find_rule(context, args.rule_id, requested=args.scope)
    draft = _edit_in_draft(found.content)
    if draft is None:
        write_line(f"No changes to '{found.id}'")
        return 0
    try:
        rule = replace(load_rule(draft), path=found.path)
        _check_conflicts(context, scope, rule=rule, removed={found.path})
        _require_loadable_by_ast_grep(context, rule)
    except ByorError as error:
        raise _with_draft_hint(error, draft) from error
    draft.unlink()
    _warn_on_id_pattern(rule)
    write_text_atomic(found.path, rule.content)
    write_line(f"Updated {scope} rule '{rule.id}' at {display_path(found.path, context.repo_root)}")
    _finish(context, fan_out=scope == "global")
    return 0


def run_remove(args: argparse.Namespace) -> int:
    context = _rule_context(args, scope=args.scope)
    scope, rule = _find_rule(context, args.rule_id, requested=args.scope)
    rule.path.unlink()
    write_line(f"Removed {scope} rule '{rule.id}' at {display_path(rule.path, context.repo_root)}")
    _finish(context, fan_out=scope == "global")
    return 0


def run_promote(args: argparse.Namespace) -> int:
    context = repo_context(args)
    if args.check is not None:
        return _promote_check(context, args.check)
    if args.from_scope is None:
        msg = "promoting a rule requires --from {local,global,package}"
        raise ByorError(msg)
    return _promote_rule(context, args)


def _promote_rule(context: RepoContext, args: argparse.Namespace) -> int:
    rule, source_dir, remove_source = _promote_source(context, args)
    project_dir = context.repo_root / context.paths.project_rules
    destination = project_dir / rule.path.relative_to(source_dir)
    if destination.exists() and not args.replace:
        msg = f"{display_path(destination, context.repo_root)} already exists; rerun with --replace to overwrite it."
        raise UnsafeOverwriteError(msg)
    # Conflict check on the post-promote state, before any write. With
    # --keep-local this fails: keeping the local original would leave project
    # and local rules sharing the ID, which ast-grep rejects.
    removed = {destination, rule.path} if remove_source else {destination}
    _check_conflicts(
        context,
        "project",
        rule=replace(rule, path=destination),
        removed=removed,
    )
    write_text_atomic(destination, rule.content)
    if remove_source:
        rule.path.unlink()
    write_line(f"Promoted '{rule.id}' to {display_path(destination, context.repo_root)}")
    _finish(context, fan_out=False)
    return 0


def _promote_source(context: RepoContext, args: argparse.Namespace) -> tuple[Rule, Path, bool]:
    if args.from_scope == "package":
        return (*_find_package_rule(context, args.rule_id), False)
    scope: RuleScope = args.from_scope
    _, rule = _find_rule(context, args.rule_id, requested=scope)
    source_dir = scope_rules_dir(
        scope, context.repo_root, paths=context.paths, global_rules_root=context.canonical.root
    )
    return rule, source_dir, scope == "local" and not args.keep_local


def _find_package_rule(context: RepoContext, rule_id: str) -> tuple[Rule, Path]:
    packages = load_installed_packages(context.canonical, load_local_config(context.repo_root).packages)
    for package in packages:
        for rule in package.rules:
            if rule.id == rule_id:
                return rule, package.root
    msg = f"No rule with ID '{rule_id}' found in installed packages."
    raise ByorError(msg)


def _promote_check(context: RepoContext, name: str) -> int:
    repo_config = load_repo_config(context.repo_root)
    if any(check.name == name for check in repo_config.checks):
        msg = f"check '{name}' is already a repo check"
        raise ByorError(msg)
    effective = load_effective_checks(context.repo_root, context.config_dir)
    match = next((check for check in effective if check.name == name), None)
    if match is None:
        msg = f"no global or package check named '{name}' to promote"
        raise ByorError(msg)
    repo_config.checks.append(match.definition)
    save_repo_config(context.repo_root, repo_config)
    write_line(f"Promoted check '{name}' into .byor/config.yml")
    return 0


def run_exclusion(args: argparse.Namespace) -> int:
    context = repo_context(args)
    local = load_local_config(context.repo_root)
    selector = _selector(args)
    values = EXCLUSION_KINDS[selector.kind].values(local)
    exclude = args.command == "exclude"
    if exclude:
        if selector.value in values:
            write_line(f"{_selector_subject(selector)} is already excluded")
        else:
            values.append(selector.value)
            save_local_config(context.repo_root, local)
            write_line(f"Excluded {_selector_subject(selector)} in .byor/local.yml")
    elif selector.value not in values:
        write_line(f"{_selector_subject(selector)} is not excluded")
    else:
        values.remove(selector.value)
        save_local_config(context.repo_root, local)
        write_line(f"Re-enabled {_selector_subject(selector)}")
    plan = _sync_and_report(context.repo_root, context.canonical)
    # A project or local rule may still own the ID: say so.
    if not exclude and selector.kind == "rule-id":
        for skipped in plan.skipped:
            if skipped.id == selector.value:
                write_line(f"'{skipped.id}' is still skipped: {skipped.reason}")
    return 0


@dataclass(frozen=True)
class ExclusionKind:
    arg: str
    """argparse attribute that carries this selector's value."""
    values: Callable[[LocalConfig], list[str]]
    """The local-config exclusion list this selector reads and writes."""
    subject: str
    """Human label; empty renders the value alone, as for a rule ID."""


EXCLUSION_KINDS: dict[str, ExclusionKind] = {
    "rule-id": ExclusionKind("rule_id", lambda local: local.excluded_rule_ids, ""),
    "rule tag": ExclusionKind("tag", lambda local: local.excluded_rule_tags, "rule tag"),
    "check": ExclusionKind("check", lambda local: local.excluded_checks, "check"),
    "check tag": ExclusionKind("check_tag", lambda local: local.excluded_check_tags, "check tag"),
}


@dataclass(frozen=True)
class ExclusionSelector:
    kind: str
    value: str


def _selector(args: argparse.Namespace) -> ExclusionSelector:
    chosen = [(kind, value) for kind, spec in EXCLUSION_KINDS.items() if (value := getattr(args, spec.arg)) is not None]
    if len(chosen) != 1:
        msg = "choose exactly one of RULE_ID, --tag, --check, or --check-tag"
        raise ByorError(msg)
    kind, value = chosen[0]
    return ExclusionSelector(kind=kind, value=value)


def _selector_subject(selector: ExclusionSelector) -> str:
    subject = EXCLUSION_KINDS[selector.kind].subject
    return f"{subject} '{selector.value}'" if subject else f"'{selector.value}'"


def _append_exception_sentence(content: str) -> str:
    """Return rule text with the standard exception sentence in its agent prompt.

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
    context: RepoContext,
    rule_id: str,
    *,
    requested: RuleScope | Literal["auto"],
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
    msg = f"No rule with ID '{rule_id}' found in {where}."
    raise ByorError(msg)


def _require_loadable_by_ast_grep(context: RepoContext, rule: Rule) -> None:
    """Reject a rule that ast-grep itself cannot load.

    Schema validation alone accepts patterns ast-grep cannot parse, and one
    such rule on disk breaks every later scan in the repo. When ast-grep is
    unavailable the check degrades to schema-only validation with a warning:
    add and edit must not hard-require ast-grep.
    """
    try:
        executable = resolve_ast_grep(load_global_config(context.config_dir).ast_grep_command)
    except AstGrepNotFoundError:
        sys.stderr.write("byor: warning: ast-grep not found; skipped checking that ast-grep can load this rule\n")
        return
    error = rule_load_error(executable, rule.content)
    if error is not None:
        msg = f"ast-grep cannot load rule '{rule.id}'; the rule was not saved:\n{error}"
        raise RuleValidationError(msg)


def _load_source_rule(source: Path) -> Rule:
    if not source.is_file():
        msg = f"{source}: no such file"
        raise ConfigError(msg)
    return load_rule(source)


def _edit_in_draft(content: str) -> Path | None:
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
    argv = [*shlex.split(os.environ.get("EDITOR") or DEFAULT_EDITOR), str(path)]
    result = subprocess.run(argv, check=False)
    if result.returncode != 0:
        msg = f"Editor exited with status {result.returncode}; aborting."
        raise ByorError(msg)


def _with_draft_hint(error: ByorError, draft: Path | None) -> ByorError:
    if draft is None:
        return error
    return type(error)(f"{error}\nYour draft is saved at {draft}.")


def _check_conflicts(
    context: RepoContext,
    scope: RuleScope,
    *,
    rule: Rule,
    removed: set[Path],
    command: bool = False,
) -> None:
    # Command rules are their own ID universe: they conflict with each other,
    # never with file rules (and vice versa).
    scoped = {
        name: [existing for existing in _scope_rules(context, name, command=command) if existing.path not in removed]
        for name in ("project", "local", "global")
    }
    scoped[scope].append(rule)
    check_id_conflicts(scoped["project"], scoped["local"], canonical_global=scoped["global"])


def _scope_rules(context: RepoContext, scope: RuleScope, *, command: bool = False) -> list[Rule]:
    if scope == "global":
        return context.canonical.command_rules if command else context.canonical.rules
    return load_rules(_scope_dir(context, scope, command=command))


def _scope_dir(context: RepoContext, scope: RuleScope, *, command: bool) -> Path:
    if not command:
        return scope_rules_dir(scope, context.repo_root, paths=context.paths, global_rules_root=context.canonical.root)
    if scope == "global":
        return context.canonical.commands_root
    command_scope: CommandRuleScope = "project" if scope == "project" else "local"
    return context.repo_root / command_rules_relpath(context.paths, command_scope)


def _warn_on_id_pattern(rule: Rule) -> None:
    for warning in rule_id_warnings([rule]):
        sys.stderr.write(f"byor: warning: {warning}\n")


def _finish(context: RepoContext, *, fan_out: bool) -> None:
    """Sync after a rule mutation and surface `doctor --quick` problems.

    Global-scope mutations fan out to every registered repo and
    reload the canonical rules the mutation just changed; everything else
    syncs the current repo with the rules already in hand. A global mutation
    made outside any byor repo has no current repo to sync, only the fan-out.
    """
    canonical = load_canonical_rules(context.config_dir) if fan_out else context.canonical
    repos = [context.repo_root] if repo_config_path(context.repo_root).is_file() else []
    if fan_out:
        repos.extend(repo for repo in iter_registered_repos(context.config_dir) if repo != context.repo_root)
    for repo in repos:
        _sync_and_report(repo, canonical)
    for problem in quick_doctor_problems(context.repo_root, context.config_dir):
        write_line(problem)


def _sync_and_report(repo_root: Path, canonical: CanonicalRules) -> SyncPlan:
    plan, result = sync_repo(repo_root, canonical)
    if result.changed:
        write_line(f"Synced {summarize_changes(result)} into {repo_root}")
    return plan
