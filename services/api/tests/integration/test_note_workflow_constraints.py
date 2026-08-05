from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint, inspect, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Inspector
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from study_agent.infrastructure.db import models as _models  # noqa: F401
from study_agent.infrastructure.db.base import Base
from study_agent.infrastructure.db.migrations import upgrade_database

SELECTED_TABLES = {
    "note_command_dedup",
    "note_generation_batches",
    "note_generation_items",
    "note_generation_attempts",
    "note_generation_inputs",
    "note_generation_outputs",
    "note_generation_events",
    "note_coverage_units",
    "note_item_inputs",
    "note_coverage_unit_results",
    "note_content_versions",
    "note_version_source_snapshots",
    "note_version_source_payloads",
    "note_version_source_links",
    "note_version_coverage",
    "note_version_coverage_units",
    "note_source_state_overlays",
}
DEFERRED_TABLES = {"note_exports", "note_export_attempts", "storage_cleanup_tasks"}
REQUIRED_CONSTRAINTS = {
    "fk_note_coverage_unit_results_item_input_scope",
    "fk_note_coverage_unit_results_attempt_scope",
    "ck_note_coverage_unit_results_reason",
    "ck_note_version_coverage_units_reason",
    "ck_note_generation_items_phase",
    "ck_note_generation_batches_style",
    "ck_note_version_coverage_units_type",
}
P1_PHASES = (
    "validating_inputs",
    "segmenting",
    "retrieving",
    "outlining",
    "generating",
    "validating_output",
    "saving",
)
UNIT_TYPES = ("slide", "pdf_section", "pdf_page_window")
SHA256 = "a" * 64
CURRENT_HEAD = "20260805_0022"
_DIALECT = postgresql.dialect()
_TEXT_CAST = re.compile(
    r"::\s*(?:character\s+varying|text)(?:\s*\[\s*\])?",
    flags=re.IGNORECASE,
)
_ANY_ARRAY = re.compile(
    r"([a-z_][a-z0-9_.]*)\s*=\s*any\s*\(\s*array\[(.*?)\]\s*\)",
    flags=re.IGNORECASE,
)
_SQL_LITERAL_TOKEN = re.compile(r"__sql_literal_(\d+)__")


@dataclass(frozen=True)
class Scope:
    user_id: str
    course_id: str
    object_id: str
    document_id: str
    note_id: str


@pytest_asyncio.fixture
async def head_engine(test_database_url: str) -> AsyncIterator[AsyncEngine]:
    await upgrade_database(test_database_url)
    engine = create_async_engine(test_database_url)
    try:
        yield engine
    finally:
        await engine.dispose()


def _new_id() -> str:
    return str(uuid4())


async def _seed_scope(connection: AsyncConnection, label: str) -> Scope:
    scope = Scope(
        user_id=_new_id(),
        course_id=_new_id(),
        object_id=_new_id(),
        document_id=_new_id(),
        note_id=_new_id(),
    )
    await connection.execute(
        text(
            "INSERT INTO users (id, subject, authentication_method) VALUES (:id, :subject, 'local')"
        ),
        {"id": scope.user_id, "subject": f"note-workflow-{label}-{scope.user_id}"},
    )
    await connection.execute(
        text(
            "INSERT INTO courses (id, user_id, title, lifecycle, row_version) "
            "VALUES (:id, :user_id, :title, 'active', 1)"
        ),
        {"id": scope.course_id, "user_id": scope.user_id, "title": f"Course {label}"},
    )
    await connection.execute(
        text(
            "INSERT INTO stored_objects "
            "(id, user_id, course_id, object_key, purpose, sha256, size_bytes, media_type) "
            "VALUES (:id, :user_id, :course_id, :object_key, 'original', :sha256, 1, "
            "'application/pdf')"
        ),
        {
            "id": scope.object_id,
            "user_id": scope.user_id,
            "course_id": scope.course_id,
            "object_key": f"note-workflow/{scope.object_id}.pdf",
            "sha256": SHA256,
        },
    )
    await connection.execute(
        text(
            "INSERT INTO documents "
            "(id, user_id, course_id, stored_object_id, filename, media_type, corpus_role, "
            "verified_sha256, status, deletion_epoch) "
            "VALUES (:id, :user_id, :course_id, :object_id, :filename, 'application/pdf', "
            "'corpus', :sha256, 'ready', 0)"
        ),
        {
            "id": scope.document_id,
            "user_id": scope.user_id,
            "course_id": scope.course_id,
            "object_id": scope.object_id,
            "filename": f"{label}.pdf",
            "sha256": SHA256,
        },
    )
    await connection.execute(
        text(
            "INSERT INTO notes "
            "(id, user_id, course_id, section_path, title, body_markdown, version, generation, "
            "generated_by_model, status) "
            "VALUES (:id, :user_id, :course_id, CAST(:section_path AS jsonb), :title, :body, "
            "1, 1, false, 'ready')"
        ),
        {
            "id": scope.note_id,
            "user_id": scope.user_id,
            "course_id": scope.course_id,
            "section_path": json.dumps(["Notes"]),
            "title": f"Note {label}",
            "body": f"Body {label}",
        },
    )
    return scope


async def _insert_batch(
    connection: AsyncConnection,
    scope: Scope,
    *,
    batch_id: str | None = None,
    command_kind: str = "create",
    mode: str = "merged",
    style: str = "exam_focus",
    retry_of_batch_id: str | None = None,
    title: str | None = "Generated note",
    title_prefix: str | None = None,
    section_path: list[str] | None = None,
    target_note_id: str | None = None,
    target_note_version: int | None = None,
    target_note_version_sha256: str | None = None,
    status: str = "queued",
    completed_at: datetime | None = None,
) -> str:
    resolved_id = batch_id or _new_id()
    resolved_path = ["Notes"] if section_path is None else section_path
    await connection.execute(
        text(
            "INSERT INTO note_generation_batches "
            "(id, user_id, course_id, mode, style, retry_of_batch_id, status, state_version, "
            "event_sequence, cancel_epoch, command_kind, title, title_prefix, section_path, "
            "target_note_id, target_note_version, target_note_version_sha256, completed_at) "
            "VALUES (:id, :user_id, :course_id, :mode, :style, :retry_of_batch_id, :status, "
            "1, 0, 0, "
            ":command_kind, :title, :title_prefix, CAST(:section_path AS jsonb), "
            ":target_note_id, :target_note_version, :target_note_version_sha256, :completed_at)"
        ),
        {
            "id": resolved_id,
            "user_id": scope.user_id,
            "course_id": scope.course_id,
            "mode": mode,
            "style": style,
            "retry_of_batch_id": retry_of_batch_id,
            "command_kind": command_kind,
            "title": title,
            "title_prefix": title_prefix,
            "section_path": json.dumps(resolved_path),
            "target_note_id": target_note_id,
            "target_note_version": target_note_version,
            "target_note_version_sha256": target_note_version_sha256,
            "status": status,
            "completed_at": completed_at,
        },
    )
    return resolved_id


async def _insert_item(
    connection: AsyncConnection,
    scope: Scope,
    batch_id: str,
    *,
    item_id: str | None = None,
    ordinal: int = 1,
    phase: str | None = None,
    status: str = "queued",
    completed_at: datetime | None = None,
) -> str:
    resolved_id = item_id or _new_id()
    await connection.execute(
        text(
            "INSERT INTO note_generation_items "
            "(id, batch_id, user_id, course_id, ordinal, status, phase, state_version, attempt, "
            "max_attempts, lease_version, cancel_epoch, completed_at) "
            "VALUES (:id, :batch_id, :user_id, :course_id, :ordinal, :status, :phase, "
            "1, 0, 3, 0, 0, :completed_at)"
        ),
        {
            "id": resolved_id,
            "batch_id": batch_id,
            "user_id": scope.user_id,
            "course_id": scope.course_id,
            "ordinal": ordinal,
            "phase": phase,
            "status": status,
            "completed_at": completed_at,
        },
    )
    return resolved_id


async def _insert_input(
    connection: AsyncConnection,
    scope: Scope,
    batch_id: str,
    *,
    input_id: str | None = None,
    document_id: str | None = None,
    ordinal: int = 1,
) -> str:
    resolved_id = input_id or _new_id()
    await connection.execute(
        text(
            "INSERT INTO note_generation_inputs "
            "(id, batch_id, user_id, course_id, ordinal, document_id, revision_id, "
            "deletion_epoch, document_name, media_type, content_sha256, index_manifest_at_submit) "
            "VALUES (:id, :batch_id, :user_id, :course_id, :ordinal, :document_id, "
            ":revision_id, 0, 'source.pdf', 'application/pdf', :sha256, :manifest)"
        ),
        {
            "id": resolved_id,
            "batch_id": batch_id,
            "user_id": scope.user_id,
            "course_id": scope.course_id,
            "ordinal": ordinal,
            "document_id": document_id or scope.document_id,
            "revision_id": _new_id(),
            "sha256": SHA256,
            "manifest": SHA256,
        },
    )
    return resolved_id


async def _insert_unit(
    connection: AsyncConnection,
    scope: Scope,
    batch_id: str,
    input_id: str,
    *,
    unit_id: str | None = None,
    ordinal: int = 1,
    unit_type: str = "slide",
) -> str:
    resolved_id = unit_id or _new_id()
    await connection.execute(
        text(
            "INSERT INTO note_coverage_units "
            "(id, input_id, batch_id, user_id, course_id, ordinal, unit_type, locator, "
            "content_sha256, is_substantive) "
            "VALUES (:id, :input_id, :batch_id, :user_id, :course_id, :ordinal, :unit_type, "
            ":locator, :sha256, true)"
        ),
        {
            "id": resolved_id,
            "input_id": input_id,
            "batch_id": batch_id,
            "user_id": scope.user_id,
            "course_id": scope.course_id,
            "ordinal": ordinal,
            "unit_type": unit_type,
            "locator": f"unit:{ordinal}",
            "sha256": SHA256,
        },
    )
    return resolved_id


async def _insert_item_input(
    connection: AsyncConnection,
    scope: Scope,
    batch_id: str,
    item_id: str,
    input_id: str,
) -> str:
    row_id = _new_id()
    await connection.execute(
        text(
            "INSERT INTO note_item_inputs "
            "(id, item_id, input_id, batch_id, user_id, course_id, ordinal) "
            "VALUES (:id, :item_id, :input_id, :batch_id, :user_id, :course_id, 1)"
        ),
        {
            "id": row_id,
            "item_id": item_id,
            "input_id": input_id,
            "batch_id": batch_id,
            "user_id": scope.user_id,
            "course_id": scope.course_id,
        },
    )
    return row_id


async def _insert_attempt(
    connection: AsyncConnection,
    scope: Scope,
    batch_id: str,
    item_id: str,
    *,
    attempt: int = 1,
) -> str:
    row_id = _new_id()
    await connection.execute(
        text(
            "INSERT INTO note_generation_attempts "
            "(id, item_id, batch_id, user_id, course_id, attempt, runner_id, "
            "contract_version, usage) "
            "VALUES (:id, :item_id, :batch_id, :user_id, :course_id, :attempt, "
            "'runner-1', 'v1', CAST(:usage AS jsonb))"
        ),
        {
            "id": row_id,
            "item_id": item_id,
            "batch_id": batch_id,
            "user_id": scope.user_id,
            "course_id": scope.course_id,
            "attempt": attempt,
            "usage": json.dumps({}),
        },
    )
    return row_id


async def _insert_result(
    connection: AsyncConnection,
    scope: Scope,
    batch_id: str,
    item_id: str,
    input_id: str,
    unit_id: str,
    *,
    status: str = "covered",
    reason_code: str | None = None,
    attempt: int = 1,
) -> str:
    row_id = _new_id()
    await connection.execute(
        text(
            "INSERT INTO note_coverage_unit_results "
            "(id, item_id, attempt, input_id, unit_id, batch_id, user_id, course_id, "
            "status, reason_code, ast_node_ids) "
            "VALUES (:id, :item_id, :attempt, :input_id, :unit_id, :batch_id, :user_id, "
            ":course_id, :status, :reason_code, CAST(:ast_node_ids AS jsonb))"
        ),
        {
            "id": row_id,
            "item_id": item_id,
            "attempt": attempt,
            "input_id": input_id,
            "unit_id": unit_id,
            "batch_id": batch_id,
            "user_id": scope.user_id,
            "course_id": scope.course_id,
            "status": status,
            "reason_code": reason_code,
            "ast_node_ids": json.dumps([]),
        },
    )
    return row_id


async def _insert_note_version(
    connection: AsyncConnection,
    scope: Scope,
    *,
    note_id: str | None = None,
    version: int = 1,
) -> None:
    await connection.execute(
        text(
            "INSERT INTO note_content_versions "
            "(note_id, version, user_id, course_id, title, section_path, body_markdown, "
            "content_ast, ast_schema_version, parser_version, body_sha256, source_set_sha256, "
            "coverage_manifest_sha256, note_version_sha256, created_by) "
            "VALUES (:note_id, :version, :user_id, :course_id, 'Version title', "
            "CAST(:section_path AS jsonb), 'Version body', CAST(:content_ast AS jsonb), "
            "'1', 'parser-v1', :sha256, :sha256, :sha256, :sha256, 'generated')"
        ),
        {
            "note_id": note_id or scope.note_id,
            "version": version,
            "user_id": scope.user_id,
            "course_id": scope.course_id,
            "section_path": json.dumps(["Notes"]),
            "content_ast": json.dumps({"type": "document"}),
            "sha256": SHA256,
        },
    )


async def _insert_version_coverage(
    connection: AsyncConnection,
    scope: Scope,
    *,
    version: int = 1,
) -> None:
    await connection.execute(
        text(
            "INSERT INTO note_version_coverage "
            "(note_id, version, user_id, course_id, policy_version, status, manifest_sha256, "
            "basis, generated_from_version) "
            "VALUES (:note_id, :version, :user_id, :course_id, 'v1', 'complete', "
            ":sha256, 'generated', NULL)"
        ),
        {
            "note_id": scope.note_id,
            "version": version,
            "user_id": scope.user_id,
            "course_id": scope.course_id,
            "sha256": SHA256,
        },
    )


async def _insert_version_unit(
    connection: AsyncConnection,
    scope: Scope,
    *,
    ordinal: int,
    status: str,
    reason_code: str | None,
    unit_type: str = "slide",
) -> str:
    row_id = _new_id()
    await connection.execute(
        text(
            "INSERT INTO note_version_coverage_units "
            "(id, note_id, version, user_id, course_id, input_id, ordinal, unit_type, locator, "
            "content_sha256, is_substantive, status, reason_code, ast_node_ids, "
            "source_snapshot_ids) "
            "VALUES (:id, :note_id, 1, :user_id, :course_id, :input_id, :ordinal, :unit_type, "
            ":locator, :sha256, true, :status, :reason_code, CAST(:ast_node_ids AS jsonb), "
            "CAST(:source_snapshot_ids AS jsonb))"
        ),
        {
            "id": row_id,
            "note_id": scope.note_id,
            "user_id": scope.user_id,
            "course_id": scope.course_id,
            "input_id": _new_id(),
            "ordinal": ordinal,
            "unit_type": unit_type,
            "locator": f"version-unit:{ordinal}",
            "sha256": SHA256,
            "status": status,
            "reason_code": reason_code,
            "ast_node_ids": json.dumps([]),
            "source_snapshot_ids": json.dumps([]),
        },
    )
    return row_id


async def _insert_output(
    connection: AsyncConnection,
    scope: Scope,
    batch_id: str,
    item_id: str,
    *,
    note_version: int,
) -> str:
    row_id = _new_id()
    await connection.execute(
        text(
            "INSERT INTO note_generation_outputs "
            "(id, batch_id, item_id, user_id, course_id, note_id, note_version) "
            "VALUES (:id, :batch_id, :item_id, :user_id, :course_id, :note_id, :note_version)"
        ),
        {
            "id": row_id,
            "batch_id": batch_id,
            "item_id": item_id,
            "user_id": scope.user_id,
            "course_id": scope.course_id,
            "note_id": scope.note_id,
            "note_version": note_version,
        },
    )
    return row_id


def _compile_sql(value: Any) -> str:
    if hasattr(value, "compile"):
        return str(
            value.compile(
                dialect=_DIALECT,
                compile_kwargs={"literal_binds": True},
            )
        )
    return str(value)


def _protect_sql_literals(expression: str) -> tuple[str, tuple[str, ...]]:
    literals: list[str] = []
    protected: list[str] = []
    index = 0
    while index < len(expression):
        if expression[index] != "'":
            protected.append(expression[index])
            index += 1
            continue

        start = index
        index += 1
        while index < len(expression):
            if expression[index] != "'":
                index += 1
                continue
            if index + 1 < len(expression) and expression[index + 1] == "'":
                index += 2
                continue
            index += 1
            break
        literal = expression[start:index]
        token = f"__sql_literal_{len(literals)}__"
        literals.append(literal)
        protected.append(token)
    return "".join(protected), tuple(literals)


def _restore_sql_literals(expression: str, literals: tuple[str, ...]) -> str:
    return _SQL_LITERAL_TOKEN.sub(
        lambda match: literals[int(match.group(1))],
        expression,
    )


def _normalize_protected_sql(
    expression: str,
    *,
    remove_text_casts: bool = False,
) -> tuple[str, tuple[str, ...]]:
    protected, literals = _protect_sql_literals(expression)
    normalized = " ".join(protected.lower().split())
    if remove_text_casts:
        normalized = _TEXT_CAST.sub("", normalized)
        normalized = " ".join(normalized.split())
    return normalized, literals


def _normalize_outside_literals(expression: str, *, remove_text_casts: bool = False) -> str:
    normalized, literals = _normalize_protected_sql(
        expression,
        remove_text_casts=remove_text_casts,
    )
    return _restore_sql_literals(normalized, literals)


def _normalize_default(value: Any | None) -> str | None:
    if value is None:
        return None
    normalized = _normalize_outside_literals(_compile_sql(value), remove_text_casts=True)
    return _strip_outer_parentheses(normalized)


def _strip_outer_parentheses(expression: str) -> str:
    candidate = expression.strip()
    while candidate.startswith("(") and candidate.endswith(")"):
        depth = 0
        quoted = False
        wraps_all = True
        index = 0
        while index < len(candidate):
            character = candidate[index]
            if character == "'":
                if quoted and index + 1 < len(candidate) and candidate[index + 1] == "'":
                    index += 2
                    continue
                quoted = not quoted
            elif not quoted:
                if character == "(":
                    depth += 1
                elif character == ")":
                    depth -= 1
                    if depth == 0 and index != len(candidate) - 1:
                        wraps_all = False
                        break
            index += 1
        if not wraps_all or depth != 0:
            break
        candidate = candidate[1:-1].strip()
    return candidate


def _split_top_level(expression: str, operator: str) -> list[str]:
    marker = f" {operator} "
    parts: list[str] = []
    start = 0
    depth = 0
    quoted = False
    index = 0
    while index < len(expression):
        character = expression[index]
        if character == "'":
            if quoted and index + 1 < len(expression) and expression[index + 1] == "'":
                index += 2
                continue
            quoted = not quoted
            index += 1
            continue
        if not quoted:
            if character == "(":
                depth += 1
            elif character == ")":
                depth -= 1
            elif depth == 0 and expression.startswith(marker, index):
                parts.append(expression[start:index].strip())
                index += len(marker)
                start = index
                continue
        index += 1
    if parts:
        parts.append(expression[start:].strip())
        return parts
    return [expression.strip()]


def _canonical_boolean(expression: str) -> tuple[str, Any]:
    candidate = _strip_outer_parentheses(expression)
    for operator in ("or", "and"):
        parts = _split_top_level(candidate, operator)
        if len(parts) > 1:
            children: list[tuple[str, Any]] = []
            for part in parts:
                child = _canonical_boolean(part)
                if child[0] == operator:
                    children.extend(child[1])
                else:
                    children.append(child)
            return operator, tuple(children)
    return "atom", _normalize_outside_literals(candidate)


def _canonical_check(expression: Any) -> tuple[str, Any]:
    normalized, literals = _normalize_protected_sql(
        _compile_sql(expression),
        remove_text_casts=True,
    )
    while True:
        replaced = _ANY_ARRAY.sub(
            lambda match: f"{match.group(1)} in ({match.group(2)})", normalized
        )
        if replaced == normalized:
            break
        normalized = replaced
    normalized = _restore_sql_literals(normalized, literals)
    return _canonical_boolean(normalized)


def _postgres_constraint_name(error: IntegrityError) -> str | None:
    pending: list[Any] = [error, error.orig]
    visited: set[int] = set()
    while pending:
        candidate = pending.pop(0)
        if candidate is None or id(candidate) in visited:
            continue
        visited.add(id(candidate))
        constraint_name = getattr(candidate, "constraint_name", None)
        if isinstance(constraint_name, str):
            return constraint_name
        diagnostics = getattr(candidate, "diag", None)
        diagnostic_name = getattr(diagnostics, "constraint_name", None)
        if isinstance(diagnostic_name, str):
            return diagnostic_name
        pending.extend(
            (
                getattr(candidate, "orig", None),
                getattr(candidate, "__cause__", None),
                getattr(candidate, "__context__", None),
            )
        )
    return None


@contextmanager
def _raises_constraint(expected_name: str) -> Iterator[None]:
    with pytest.raises(IntegrityError) as caught:
        yield
    assert _postgres_constraint_name(caught.value) == expected_name


def _type_signature(type_: Any) -> tuple[str, int | None, int | None, int | None]:
    return (
        " ".join(str(type_.compile(dialect=_DIALECT)).lower().split()),
        getattr(type_, "length", None),
        getattr(type_, "precision", None),
        getattr(type_, "scale", None),
    )


def _metadata_table_map(table_name: str) -> dict[str, Any]:
    table = Base.metadata.tables[table_name]
    columns = {
        column.name: (
            _type_signature(column.type),
            bool(column.nullable),
            _normalize_default(column.server_default.arg if column.server_default else None),
        )
        for column in table.columns
    }
    primary_key = tuple(column.name for column in table.primary_key.columns)
    unique_constraints = {
        constraint.name: tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint) and constraint.name is not None
    }
    checks = {
        constraint.name: _canonical_check(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint) and constraint.name is not None
    }
    foreign_keys = {
        constraint.name: (
            tuple(column.name for column in constraint.columns),
            constraint.referred_table.schema,
            constraint.referred_table.name,
            tuple(element.column.name for element in constraint.elements),
            constraint.ondelete.upper() if constraint.ondelete else None,
            constraint.onupdate.upper() if constraint.onupdate else None,
            constraint.deferrable,
            constraint.initially.upper() if constraint.initially else None,
        )
        for constraint in table.constraints
        if isinstance(constraint, ForeignKeyConstraint) and constraint.name is not None
    }
    indexes: dict[str, Any] = {}
    for index in table.indexes:
        expressions = tuple(
            expression.name
            if getattr(expression, "name", None) is not None
            else " ".join(_compile_sql(expression).lower().split())
            for expression in index.expressions
        )
        predicate = index.dialect_options["postgresql"].get("where")
        indexes[index.name] = (
            expressions,
            bool(index.unique),
            _canonical_check(predicate) if predicate is not None else None,
        )
    return {
        "columns": columns,
        "primary_key": primary_key,
        "unique_constraints": unique_constraints,
        "checks": checks,
        "foreign_keys": foreign_keys,
        "indexes": indexes,
    }


def _live_table_map(inspector: Inspector, table_name: str) -> dict[str, Any]:
    columns = {
        column["name"]: (
            _type_signature(column["type"]),
            bool(column["nullable"]),
            _normalize_default(column.get("default")),
        )
        for column in inspector.get_columns(table_name)
    }
    primary_key = tuple(inspector.get_pk_constraint(table_name)["constrained_columns"])
    unique_constraints = {
        constraint["name"]: tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints(table_name)
        if constraint["name"] is not None
    }
    checks = {
        constraint["name"]: _canonical_check(constraint["sqltext"])
        for constraint in inspector.get_check_constraints(table_name)
        if constraint["name"] is not None
    }
    foreign_keys: dict[str, Any] = {}
    for constraint in inspector.get_foreign_keys(table_name):
        options = constraint.get("options") or {}
        foreign_keys[constraint["name"]] = (
            tuple(constraint["constrained_columns"]),
            constraint.get("referred_schema"),
            constraint["referred_table"],
            tuple(constraint["referred_columns"]),
            options.get("ondelete", "").upper() or None,
            options.get("onupdate", "").upper() or None,
            options.get("deferrable"),
            options.get("initially", "").upper() or None,
        )
    indexes: dict[str, Any] = {}
    for index in inspector.get_indexes(table_name):
        if index.get("duplicates_constraint") is not None:
            continue
        column_names = index.get("column_names") or []
        reflected_expressions = index.get("expressions") or []
        expressions: list[str] = []
        for position, column_name in enumerate(column_names):
            if column_name is not None:
                expressions.append(column_name)
            else:
                expressions.append(" ".join(str(reflected_expressions[position]).lower().split()))
        dialect_options = index.get("dialect_options") or {}
        predicate = dialect_options.get("postgresql_where")
        indexes[index["name"]] = (
            tuple(expressions),
            bool(index["unique"]),
            _canonical_check(predicate) if predicate is not None else None,
        )
    return {
        "columns": columns,
        "primary_key": primary_key,
        "unique_constraints": unique_constraints,
        "checks": checks,
        "foreign_keys": foreign_keys,
        "indexes": indexes,
    }


def test_sql_canonicalizers_preserve_quoted_literal_content() -> None:
    assert _normalize_default("('FAILED')") == "'FAILED'"
    assert _normalize_default("('FAILED'::text)") == "'FAILED'"
    assert _normalize_default("('it''s FAILED::text'::text)") == "'it''s FAILED::text'"
    assert _normalize_default("('FAILED')") != _normalize_default("('failed')")
    assert _normalize_default("'x::text'") != _normalize_default("'x'")

    assert _canonical_check("STATUS::text = 'FAILED'") == _canonical_check("status = 'FAILED'")
    assert _canonical_check("status = 'FAILED'") != _canonical_check("status = 'failed'")
    assert _canonical_check("value = 'x::text'") != _canonical_check("value = 'x'")
    assert _canonical_check("label = 'it''s FAILED::text'") != _canonical_check(
        "label = 'it''s failed::text'"
    )
    assert _canonical_check("label = 'x  y'") != _canonical_check("label = 'x y'")
    assert _canonical_check("message = 'it''s status = ANY (ARRAY[foo])'") != _canonical_check(
        "message = 'it''s status IN (foo)'"
    )
    assert _canonical_check(
        "STATUS::text = ANY (ARRAY['FAILED'::character varying, "
        "'it''s x::text'::character varying]::text[])"
    ) == _canonical_check("status IN ('FAILED', 'it''s x::text')")


@pytest.mark.integration
async def test_note_workflow_inventory_head_and_temporary_defaults(
    head_engine: AsyncEngine,
) -> None:
    async with head_engine.connect() as connection:
        head = await connection.scalar(text("SELECT version_num FROM alembic_version"))
        tables = set(
            (
                await connection.execute(
                    text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
                )
            ).scalars()
        )
        constraints = set(
            (
                await connection.execute(
                    text(
                        "SELECT constraint_name FROM information_schema.table_constraints "
                        "WHERE table_schema = 'public'"
                    )
                )
            ).scalars()
        )
        batch_columns = {
            row.column_name: (row.is_nullable, row.column_default)
            for row in (
                await connection.execute(
                    text(
                        "SELECT column_name, is_nullable, column_default "
                        "FROM information_schema.columns "
                        "WHERE table_schema = 'public' "
                        "AND table_name = 'note_generation_batches'"
                    )
                )
            )
        }
        output_columns = {
            row.column_name: (row.is_nullable, row.column_default)
            for row in (
                await connection.execute(
                    text(
                        "SELECT column_name, is_nullable, column_default "
                        "FROM information_schema.columns "
                        "WHERE table_schema = 'public' "
                        "AND table_name = 'note_generation_outputs'"
                    )
                )
            )
        }

    assert head == CURRENT_HEAD
    assert tables >= SELECTED_TABLES
    assert DEFERRED_TABLES.isdisjoint(tables)
    assert constraints >= REQUIRED_CONSTRAINTS
    assert {
        "command_kind",
        "style",
        "title",
        "title_prefix",
        "section_path",
        "target_note_id",
        "target_note_version",
        "target_note_version_sha256",
    } <= batch_columns.keys()
    assert output_columns["note_version"] == ("NO", None)
    assert batch_columns["command_kind"] == ("NO", None)
    assert batch_columns["style"] == ("NO", None)
    assert batch_columns["section_path"] == ("NO", None)


@pytest.mark.integration
async def test_composite_foreign_keys_reject_cross_scope_and_wrong_versions(
    head_engine: AsyncEngine,
) -> None:
    async with head_engine.begin() as connection:
        scope_a = await _seed_scope(connection, "scope-a")
        scope_b = await _seed_scope(connection, "scope-b")
        parent_batch = await _insert_batch(connection, scope_a)
        batch_a = await _insert_batch(connection, scope_a)
        await _insert_note_version(connection, scope_a)
        item_a = await _insert_item(connection, scope_a, batch_a)

    with pytest.raises(IntegrityError):
        async with head_engine.begin() as connection:
            await _insert_batch(
                connection,
                scope_b,
                command_kind="retry_failed",
                retry_of_batch_id=parent_batch,
            )

    with pytest.raises(IntegrityError):
        async with head_engine.begin() as connection:
            await _insert_input(
                connection,
                scope_a,
                batch_a,
                document_id=scope_b.document_id,
            )

    with pytest.raises(IntegrityError):
        async with head_engine.begin() as connection:
            await _insert_input(connection, scope_b, batch_a, document_id=scope_b.document_id)

    with pytest.raises(IntegrityError):
        async with head_engine.begin() as connection:
            await _insert_item(connection, scope_b, batch_a)

    with pytest.raises(IntegrityError):
        async with head_engine.begin() as connection:
            await _insert_note_version(
                connection,
                scope_b,
                note_id=scope_a.note_id,
                version=2,
            )

    with pytest.raises(IntegrityError):
        async with head_engine.begin() as connection:
            await _insert_batch(
                connection,
                scope_b,
                command_kind="regeneration",
                target_note_id=scope_a.note_id,
                target_note_version=1,
                target_note_version_sha256=SHA256,
            )

    with pytest.raises(IntegrityError):
        async with head_engine.begin() as connection:
            await _insert_output(
                connection,
                scope_a,
                batch_a,
                item_a,
                note_version=2,
            )


@pytest.mark.integration
async def test_coverage_result_requires_explicit_item_input_membership(
    head_engine: AsyncEngine,
) -> None:
    async with head_engine.begin() as connection:
        scope = await _seed_scope(connection, "membership")
        batch_id = await _insert_batch(connection, scope)
        item_id = await _insert_item(connection, scope, batch_id)
        input_id = await _insert_input(connection, scope, batch_id)
        unit_id = await _insert_unit(connection, scope, batch_id, input_id)
        await _insert_attempt(connection, scope, batch_id, item_id)

    with pytest.raises(IntegrityError):
        async with head_engine.begin() as connection:
            await _insert_result(
                connection,
                scope,
                batch_id,
                item_id,
                input_id,
                unit_id,
            )


@pytest.mark.integration
async def test_coverage_result_requires_exact_attempt_and_succeeds_after_both_links(
    head_engine: AsyncEngine,
) -> None:
    async with head_engine.begin() as connection:
        scope = await _seed_scope(connection, "attempt")
        batch_id = await _insert_batch(connection, scope)
        item_id = await _insert_item(connection, scope, batch_id)
        input_id = await _insert_input(connection, scope, batch_id)
        unit_id = await _insert_unit(connection, scope, batch_id, input_id)
        await _insert_item_input(connection, scope, batch_id, item_id, input_id)

    with pytest.raises(IntegrityError):
        async with head_engine.begin() as connection:
            await _insert_result(
                connection,
                scope,
                batch_id,
                item_id,
                input_id,
                unit_id,
            )

    async with head_engine.begin() as connection:
        await _insert_attempt(connection, scope, batch_id, item_id)
        result_id = await _insert_result(
            connection,
            scope,
            batch_id,
            item_id,
            input_id,
            unit_id,
        )

    async with head_engine.connect() as connection:
        persisted = await connection.scalar(
            text("SELECT count(*) FROM note_coverage_unit_results WHERE id = :id"),
            {"id": result_id},
        )
    assert persisted == 1


@pytest.mark.integration
async def test_task_and_version_coverage_reason_and_type_rules(
    head_engine: AsyncEngine,
) -> None:
    async with head_engine.begin() as connection:
        scope = await _seed_scope(connection, "coverage-rules")
        batch_id = await _insert_batch(connection, scope)
        item_id = await _insert_item(connection, scope, batch_id)
        input_id = await _insert_input(connection, scope, batch_id)
        await _insert_item_input(connection, scope, batch_id, item_id, input_id)
        await _insert_attempt(connection, scope, batch_id, item_id)
        await _insert_note_version(connection, scope)
        await _insert_version_coverage(connection, scope)

        for ordinal, (status, reason_code) in enumerate(
            (
                ("covered", None),
                ("skipped", "not-substantive"),
                ("failed", "provider-error"),
            ),
            start=1,
        ):
            unit_id = await _insert_unit(
                connection,
                scope,
                batch_id,
                input_id,
                ordinal=ordinal,
                unit_type=UNIT_TYPES[(ordinal - 1) % len(UNIT_TYPES)],
            )
            await _insert_result(
                connection,
                scope,
                batch_id,
                item_id,
                input_id,
                unit_id,
                status=status,
                reason_code=reason_code,
            )

        for ordinal, (status, reason_code, unit_type) in enumerate(
            (
                ("pending", None, "slide"),
                ("covered", None, "pdf_section"),
                ("skipped", "not-substantive", "pdf_page_window"),
                ("failed", "provider-error", "slide"),
            ),
            start=1,
        ):
            await _insert_version_unit(
                connection,
                scope,
                ordinal=ordinal,
                status=status,
                reason_code=reason_code,
                unit_type=unit_type,
            )

        rejected_task_units: list[tuple[str, str | None, str]] = []
        for ordinal, (status, reason_code) in enumerate(
            (
                ("covered", "unexpected"),
                ("skipped", None),
                ("skipped", "   "),
                ("failed", None),
                ("failed", "   "),
            ),
            start=10,
        ):
            rejected_task_units.append(
                (
                    status,
                    reason_code,
                    await _insert_unit(
                        connection,
                        scope,
                        batch_id,
                        input_id,
                        ordinal=ordinal,
                    ),
                )
            )

    for status, reason_code, unit_id in rejected_task_units:
        with pytest.raises(IntegrityError):
            async with head_engine.begin() as connection:
                await _insert_result(
                    connection,
                    scope,
                    batch_id,
                    item_id,
                    input_id,
                    unit_id,
                    status=status,
                    reason_code=reason_code,
                )

    for ordinal, (status, reason_code) in enumerate(
        (
            ("pending", "unexpected"),
            ("covered", "unexpected"),
            ("skipped", None),
            ("skipped", "   "),
            ("failed", None),
            ("failed", "   "),
        ),
        start=20,
    ):
        with pytest.raises(IntegrityError):
            async with head_engine.begin() as connection:
                await _insert_version_unit(
                    connection,
                    scope,
                    ordinal=ordinal,
                    status=status,
                    reason_code=reason_code,
                )

    with pytest.raises(IntegrityError):
        async with head_engine.begin() as connection:
            await _insert_version_unit(
                connection,
                scope,
                ordinal=30,
                status="pending",
                reason_code=None,
                unit_type="unknown",
            )


@pytest.mark.integration
async def test_generation_item_phase_accepts_only_p1_values(
    head_engine: AsyncEngine,
) -> None:
    async with head_engine.begin() as connection:
        scope = await _seed_scope(connection, "phases")
        batch_id = await _insert_batch(connection, scope)
        await _insert_item(connection, scope, batch_id, ordinal=1, phase=None)
        for ordinal, phase in enumerate(P1_PHASES, start=2):
            await _insert_item(
                connection,
                scope,
                batch_id,
                ordinal=ordinal,
                phase=phase,
            )

    with pytest.raises(IntegrityError):
        async with head_engine.begin() as connection:
            await _insert_item(
                connection,
                scope,
                batch_id,
                ordinal=len(P1_PHASES) + 2,
                phase="publishing",
            )


@pytest.mark.integration
async def test_batch_and_item_terminal_time_constraints(
    head_engine: AsyncEngine,
) -> None:
    completed_at = datetime(2026, 7, 23, 12, 0, tzinfo=UTC)
    async with head_engine.begin() as connection:
        scope = await _seed_scope(connection, "terminal-times")
        item_batch = await _insert_batch(connection, scope)
        await _insert_batch(
            connection,
            scope,
            status="succeeded",
            completed_at=completed_at,
        )
        await _insert_batch(connection, scope, status="queued", completed_at=None)
        await _insert_item(
            connection,
            scope,
            item_batch,
            ordinal=1,
            status="succeeded",
            completed_at=completed_at,
        )
        await _insert_item(
            connection,
            scope,
            item_batch,
            ordinal=2,
            status="queued",
            completed_at=None,
        )

    with _raises_constraint("ck_note_generation_batches_terminal_time"):
        async with head_engine.begin() as connection:
            await _insert_batch(connection, scope, status="succeeded", completed_at=None)

    with _raises_constraint("ck_note_generation_batches_terminal_time"):
        async with head_engine.begin() as connection:
            await _insert_batch(
                connection,
                scope,
                status="queued",
                completed_at=completed_at,
            )

    with _raises_constraint("ck_note_generation_items_terminal_time"):
        async with head_engine.begin() as connection:
            await _insert_item(
                connection,
                scope,
                item_batch,
                ordinal=3,
                status="succeeded",
                completed_at=None,
            )

    with _raises_constraint("ck_note_generation_items_terminal_time"):
        async with head_engine.begin() as connection:
            await _insert_item(
                connection,
                scope,
                item_batch,
                ordinal=4,
                status="queued",
                completed_at=completed_at,
            )


@pytest.mark.integration
async def test_batch_command_target_title_style_and_output_rules(
    head_engine: AsyncEngine,
) -> None:
    async with head_engine.begin() as connection:
        scope = await _seed_scope(connection, "commands")
        await _insert_note_version(connection, scope)
        parent_batch = await _insert_batch(connection, scope)
        await _insert_batch(
            connection,
            scope,
            mode="per_document",
            title=None,
            title_prefix="Document",
        )
        await _insert_batch(
            connection,
            scope,
            command_kind="retry_failed",
            retry_of_batch_id=parent_batch,
        )
        await _insert_batch(
            connection,
            scope,
            command_kind="retry_gaps",
            retry_of_batch_id=parent_batch,
        )
        await _insert_batch(
            connection,
            scope,
            command_kind="regeneration",
            target_note_id=scope.note_id,
            target_note_version=1,
            target_note_version_sha256=SHA256,
        )
        output_items = [
            await _insert_item(
                connection,
                scope,
                parent_batch,
                ordinal=ordinal,
            )
            for ordinal in range(1, 5)
        ]
        await _insert_output(
            connection,
            scope,
            parent_batch,
            output_items[0],
            note_version=1,
        )

    with pytest.raises(IntegrityError):
        async with head_engine.begin() as connection:
            await _insert_batch(
                connection,
                scope,
                command_kind="create",
                retry_of_batch_id=parent_batch,
            )

    with pytest.raises(IntegrityError):
        async with head_engine.begin() as connection:
            await _insert_batch(connection, scope, command_kind="retry_failed")

    with pytest.raises(IntegrityError):
        async with head_engine.begin() as connection:
            await _insert_batch(
                connection,
                scope,
                command_kind="regeneration",
                mode="per_document",
                title=None,
                title_prefix="Document",
                target_note_id=scope.note_id,
                target_note_version=1,
                target_note_version_sha256=SHA256,
            )

    with pytest.raises(IntegrityError):
        async with head_engine.begin() as connection:
            await _insert_batch(connection, scope, command_kind="regeneration")

    with pytest.raises(IntegrityError):
        async with head_engine.begin() as connection:
            await _insert_batch(
                connection,
                scope,
                target_note_id=scope.note_id,
                target_note_version=1,
                target_note_version_sha256=SHA256,
            )

    with pytest.raises(IntegrityError):
        async with head_engine.begin() as connection:
            await _insert_batch(connection, scope, title_prefix="Not allowed")

    with pytest.raises(IntegrityError):
        async with head_engine.begin() as connection:
            await _insert_batch(
                connection,
                scope,
                mode="per_document",
                title="Not allowed",
            )

    with pytest.raises(IntegrityError):
        async with head_engine.begin() as connection:
            await _insert_batch(connection, scope, section_path=[])

    with _raises_constraint("ck_note_generation_batches_command_kind"):
        async with head_engine.begin() as connection:
            await _insert_batch(connection, scope, command_kind="unknown")

    with _raises_constraint("ck_note_generation_batches_style"):
        async with head_engine.begin() as connection:
            await _insert_batch(connection, scope, style="unknown")

    with _raises_constraint("ck_note_generation_batches_target_version"):
        async with head_engine.begin() as connection:
            await _insert_batch(
                connection,
                scope,
                command_kind="regeneration",
                target_note_id=scope.note_id,
                target_note_version=0,
                target_note_version_sha256=SHA256,
            )

    with pytest.raises(IntegrityError):
        async with head_engine.begin() as connection:
            await _insert_batch(
                connection,
                scope,
                command_kind="regeneration",
                target_note_id=scope.note_id,
                target_note_version=1,
                target_note_version_sha256="not-a-hash",
            )

    with pytest.raises(IntegrityError):
        async with head_engine.begin() as connection:
            await _insert_output(
                connection,
                scope,
                parent_batch,
                output_items[1],
                note_version=1,
            )

    with pytest.raises(IntegrityError):
        async with head_engine.begin() as connection:
            await _insert_output(
                connection,
                scope,
                parent_batch,
                output_items[2],
                note_version=2,
            )

    with _raises_constraint("ck_note_generation_outputs_note_version"):
        async with head_engine.begin() as connection:
            await _insert_output(
                connection,
                scope,
                parent_batch,
                output_items[3],
                note_version=0,
            )


@pytest.mark.integration
async def test_live_postgresql_schema_matches_orm_metadata_for_all_selected_tables(
    head_engine: AsyncEngine,
) -> None:
    async with head_engine.connect() as connection:
        live_maps = await connection.run_sync(
            lambda sync_connection: {
                table_name: _live_table_map(inspect(sync_connection), table_name)
                for table_name in SELECTED_TABLES
            }
        )

    metadata_maps = {table_name: _metadata_table_map(table_name) for table_name in SELECTED_TABLES}
    assert live_maps == metadata_maps
