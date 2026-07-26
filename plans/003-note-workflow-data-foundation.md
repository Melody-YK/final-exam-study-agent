# Plan 003: Land the demo-critical note persistence foundation

> **Executor instructions**: Read this plan completely before changing code. Follow each step in
> order and run every verification command. The checkpoint migration and ORM are mixed across
> demo-critical persistence, later behavior, and explicitly deferred export/cleanup work. Extract
> only the symbols and table blocks listed here. If any STOP condition occurs, stop and report the
> exact command, file, and dependency; do not widen the slice.
>
> **Drift check (run first)**:
>
> ```bash
> BASE_SHA=d8fe9d0528b9eb58e286b5a2910a280e81530de2
> SOURCE_SHA=7c08333bb22ca7b7046c2f82ddced5794e8fb78a
> test "$(git rev-parse origin/main)" = "$BASE_SHA"
> test "$(git rev-parse codex/workflow-checkpoint)" = "$SOURCE_SHA"
> test "$(git merge-base "$BASE_SHA" "$SOURCE_SHA")" = \
>   "856cb1d5b39241e6591b0396a161764649dc0832"
> git diff --stat "$BASE_SHA"..origin/main -- \
>   services/api/alembic/versions \
>   services/api/src/study_agent/infrastructure/db/models \
>   services/api/tests/integration/test_answering_migration.py \
>   services/api/tests/integration/test_migrations.py \
>   services/api/tests/integration/test_note_workflow_constraints.py
> ```
>
> Expected: all three `test` commands exit 0 and the final command prints no output. If the base,
> source, merge-base, or in-scope paths drifted, STOP and report before extracting anything.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MED
- **Depends on**: `plans/002-note-workflow-contract-capability.md` (DONE and merged)
- **Category**: migration / correctness / tests
- **Planned at**: commit `d8fe9d0`, 2026-07-23
- **Source checkpoint**: `codex/workflow-checkpoint@7c08333`
- **Target branch**: `codex/note-workflow-data-foundation`
- **Target commit shape**: one independently verifiable commit; do not push or open a PR
- **Execution status**: DONE, independently reconciled at commit `f5cca9d` on 2026-07-23;
  the foundation was subsequently included in squash-merged `main@a67dc87`

### Reconciliation evidence

- Branch `codex/note-workflow-data-foundation` is one clean local commit above
  `origin/main@d8fe9d0`, with no upstream, push, merge, or PR.
- The diff contains exactly the eight allowlisted files. Migration `7102` has exactly 17
  `create_table` and 17 `drop_table` calls, and the deferred export/cleanup boundary is clean.
- PostgreSQL-focused tests passed (`18 passed`), Alembic reports only
  `20260722_0008 (head)`, and `alembic check` reports no new upgrade operations.
- Full non-live Python passed (`543 passed, 1 deselected`); P1 contract regression passed
  (`56 passed`); Web Vitest passed (`40 passed`); QA/Library Playwright passed (`20 passed`).
- Web typecheck/lint, mypy, Ruff format/lint, generated OpenAPI/TypeScript equality, and
  `git diff --check` all passed in the final worktree.
- Before later behavior writes multiple output versions for one Note, revisit the 0008 downgrade:
  restoring the older note-level uniqueness constraint can fail once such rows exist. This does
  not block the current dormant persistence slice.

## Why this matters

The local demo path needs durable task, input, item, coverage, output, and immutable Note-version
facts before any repository, runner, or Web workflow can be isolated safely. The checkpoint already
contains those tables, but its first migration also embeds deferred DOCX export, object cleanup,
and a global StoredObject purpose change, while its ORM reflects a later `0008` schema state. This
slice lands only the task/version foundation, preserves ORM/migration/contract parity, and adds real
PostgreSQL negative tests before behavior is built on top of it.

This commit is intentionally dormant. It does not make task creation, state transitions, Note
generation, history APIs, or online preview available. Those remain later dependent slices.

## Current state

- `origin/main@d8fe9d0` has one Alembic head, `20260721_0007`, and contains the completed query
  continuity plus default-off note-workflow contract/capability slices.
- The checkpoint migration chain is `20260721_0007 -> 7102eb21ee91 -> 20260722_0008`.
  `7102eb21ee91_note_workflow_expand.py:15` is correctly based on migration 0007.
- The checkpoint's `7102` is mixed. It changes StoredObject purpose at lines 22-44 and creates
  `storage_cleanup_tasks`, `note_exports`, and `note_export_attempts` at lines 160-222, 426-550,
  and 926-985. Those blocks are explicitly deferred and must be removed from this slice.
- The same migration creates 10 demo-critical task tables and 7 immutable version/source tables.
  These are additive and depend only on main's existing course/document/Note schema.
- `20260722_0008_note_batch_commands.py:21-149` adds command/title/target fields to batches and an
  exact `note_version` FK to outputs. The checkpoint ORM already maps these fields at
  `note_workflow.py:55-149` and `note_workflow.py:529-563`. Keeping the ORM but omitting 0008 would
  produce schema drift, so 0008 belongs in this persistence slice even though no P4 route lands.
- `NoteCoverageUnitResultModel` references an item and a unit separately but does not prove that
  the input belongs to the item or that its attempt row exists. The available unique keys are
  `note_item_inputs(item_id,input_id,batch_id,course_id,user_id)` and
  `note_generation_attempts(item_id,attempt,batch_id,course_id,user_id)`.
- The landed P1 contract requires `pending`/`covered` coverage units to have no reason and
  `skipped`/`failed` units to have a non-empty reason. The checkpoint CHECK expressions permit
  covered reasons and reject a valid pending version unit.
- The P1 `NoteGenerationPhase` contract permits exactly `validating_inputs`, `segmenting`,
  `retrieving`, `outlining`, `generating`, `validating_output`, and `saving`; the checkpoint item
  `phase` column has no database CHECK.
- `test_note_workflow_constraints.py` currently checks only two cases, omits
  `note_item_inputs`/`note_generation_outputs` from its table inventory, and mostly asserts the
  deferred export/storage constraints. `test_answering_migration.py:59` still expects head 0007.
- The AIWF status correctly says P2 remains in progress and recommends PostgreSQL expand/constraint
  validation. Treat those documents as design intent, not implementation evidence; do not edit
  `.claude/planning/**` in this slice.
- Repository conventions are additive Alembic migrations, SQLAlchemy 2 typed mappings, named
  PostgreSQL constraints, async pytest integration tests against a disposable database, Ruff with
  100-character lines, and strict mypy.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Bootstrap | `uv sync --all-packages && npm install` | exit 0; lockfiles unchanged |
| Alembic head | `uv run alembic -c services/api/alembic.ini heads` | exactly `20260722_0008 (head)` |
| Focused DB | `uv run pytest services/api/tests/integration/test_note_workflow_constraints.py services/api/tests/integration/test_migrations.py services/api/tests/integration/test_answering_migration.py services/api/tests/integration/test_notes.py -q` | all collected tests pass |
| Contract regression | `uv run pytest packages/contracts/python/tests/test_note_workflow_contracts.py -q` | all collected tests pass |
| Full Python | `uv run pytest -m "not live" -q` | all collected tests pass |
| Generate API | `npm run generate:api` | exit 0; generated artifacts remain byte-identical to base |
| Web tests | `npm test --workspace @study-agent/web` | all collected tests pass |
| Web typecheck | `npm run typecheck --workspace @study-agent/web` | exit 0, no errors |
| Web lint | `npm run lint --workspace @study-agent/web` | exit 0, no errors |
| Existing E2E | `npm run test:e2e -- tests/e2e/qa-notes.spec.ts tests/e2e/library.spec.ts` | 20 passed across configured projects |
| Python typecheck | `MYPYPATH=packages/contracts/python/src:services/api/src:services/worker/src uv run mypy -p study_contracts -p study_agent -p study_worker` | `Success: no issues found` |
| Python format | `uv run ruff format --check .` | exit 0 |
| Python lint | `uv run ruff check .` | exit 0 |
| Diff hygiene | `git diff --check "$BASE_SHA"` | no output |

Every PostgreSQL command must use `TEST_DATABASE_URL` pointing to an explicitly disposable database
whose database name contains `test`. The guard in Step 0 must run in the same shell before the first
integration test; re-run it after opening a new shell. Never let pytest fall back to its local socket
default, and never run upgrade/downgrade tests against a development or user database.

## Scope

### In scope: the only source/test files that may change

- `services/api/alembic/versions/7102eb21ee91_note_workflow_expand.py` (create a pruned migration)
- `services/api/alembic/versions/20260722_0008_note_batch_commands.py` (whole checkpoint file)
- `services/api/src/study_agent/infrastructure/db/models/note_versions.py` (new P2 ORM file)
- `services/api/src/study_agent/infrastructure/db/models/note_workflow.py` (task/version-related
  classes only; no export/cleanup classes)
- `services/api/src/study_agent/infrastructure/db/models/__init__.py` (note task/version imports and
  exports only)
- `services/api/tests/integration/test_note_workflow_constraints.py` (create and strengthen)
- `services/api/tests/integration/test_migrations.py` (add scoped 0007/head round-trip regression)
- `services/api/tests/integration/test_answering_migration.py` (update the single head assertion)

### Allowed ORM symbols

`note_workflow.py` may contain exactly these new checkpoint classes:

- `NoteGenerationBatchModel`
- `NoteGenerationInputModel`
- `NoteCoverageUnitModel`
- `NoteGenerationItemModel`
- `NoteItemInputModel`
- `NoteCoverageUnitResultModel`
- `NoteGenerationOutputModel`
- `NoteGenerationAttemptModel`
- `NoteGenerationEventModel`
- `NoteCommandDedupModel`

`note_versions.py` may contain exactly these seven classes:

- `NoteContentVersionModel`
- `NoteVersionSourceSnapshotModel`
- `NoteVersionSourcePayloadModel`
- `NoteVersionSourceLinkModel`
- `NoteVersionCoverageModel`
- `NoteVersionCoverageUnitModel`
- `NoteSourceStateOverlayModel`

### Out of scope: do not touch even if checkpoint code imports it

- `StoredObjectModel`, `StoragePurpose`, `ObjectScope`, or any provider/storage protocol.
- `note_exports`, `note_export_attempts`, `storage_cleanup_tasks`, `note-export`, DOCX, renderer,
  download, retention, or cleanup behavior/schema.
- `packages/contracts/python/**`, OpenAPI JSON, generated TypeScript, public routes, API schemas,
  or Web API clients. Generated artifacts may be regenerated only to prove they do not change.
- `services/api/src/study_agent/modules/notes/**`, including AST/canonical, version repository,
  backfill, lifecycle, state machine, task repository, batch service, runner, export, and cleanup.
- `services/api/src/study_agent/api/routers/**`, `services/api/src/study_agent/main.py`, and all Web
  product components/E2E mocks/specs.
- `.claude/planning/**`, `.idea/**`, dependency manifests/locks, exact ETA, external queues,
  observability, and unrelated refactors.

## Git workflow

- Start from the exact clean base `origin/main@d8fe9d0` in an isolated worktree.
- Branch name: `codex/note-workflow-data-foundation`.
- Use `codex/workflow-checkpoint@7c08333` only as a read-only blob source.
- Produce one commit: `feat: add note workflow data foundation`.
- Do not push, merge, open a PR, or update AIWF status documents.

## Steps

### Step 0: Prove the clean target and source topology

Run the drift block at the top of this plan. In the target worktree, also run:

```bash
BASE_SHA=d8fe9d0528b9eb58e286b5a2910a280e81530de2
SOURCE_SHA=7c08333bb22ca7b7046c2f82ddced5794e8fb78a
test "$(git rev-parse HEAD)" = "$BASE_SHA"
test "$(git branch --show-current)" = "codex/note-workflow-data-foundation"
test -z "$(git status --porcelain)"
git rev-list --count "$BASE_SHA"..HEAD
```

Expected: the target is the named branch at the exact base, clean, with zero commits ahead. If the
branch/worktree already contains work or has an upstream, STOP rather than reusing it blindly.

Ignored dependency environments are not carried into a fresh worktree. Bootstrap them before
editing and prove the manifests did not drift:

```bash
uv sync --all-packages
npm install
git diff --exit-code "$BASE_SHA" -- uv.lock package-lock.json
git status --short --branch
```

Expected: both installs succeed, the lockfiles remain byte-identical to base, and the worktree is
still clean. If bootstrap changes a lockfile, STOP and report toolchain drift.

Before any integration/full-suite command can instantiate its autouse database cleanup fixture,
export and validate the disposable database URL in the same shell:

```bash
: "${TEST_DATABASE_URL:?Set TEST_DATABASE_URL to a disposable PostgreSQL test database}"
export TEST_DATABASE_URL
uv run python - <<'PY'
import os
from sqlalchemy.engine import make_url

database_name = make_url(os.environ["TEST_DATABASE_URL"]).database or ""
if "test" not in database_name.lower():
    raise SystemExit("Refusing a database whose name does not contain 'test'")
PY
```

Expected: exit 0. The integration fixture truncates public tables, so STOP before pytest if the URL
is absent, cannot be parsed, or does not name an explicitly disposable `test` database. Re-run this
guard after every new shell or lost environment.

### Step 1: Build the pruned migration chain

Create `7102eb21ee91_note_workflow_expand.py` from checkpoint table blocks, not by copying the whole
file. Preserve:

```python
revision = "7102eb21ee91"
down_revision = "20260721_0007"
```

Its `upgrade()` must create exactly these 17 tables, with the checkpoint's columns, indexes, named
CHECKs, and scope FKs unless this plan explicitly strengthens them:

```text
note_command_dedup
note_generation_batches
note_generation_items
note_generation_attempts
note_generation_inputs
note_generation_outputs
note_generation_events
note_coverage_units
note_item_inputs
note_coverage_unit_results
note_content_versions
note_version_source_snapshots
note_version_source_payloads
note_version_source_links
note_version_coverage
note_version_coverage_units
note_source_state_overlays
```

Preserve a valid creation order for referenced unique keys and a reverse dependency order in
`downgrade()`. Do not alter any pre-existing table in 7102.

Strengthen 7102 in both migration and ORM shape:

1. Add `fk_note_coverage_unit_results_item_input_scope` from
   `(item_id,input_id,batch_id,course_id,user_id)` to the matching unique key in
   `note_item_inputs`.
2. Add `fk_note_coverage_unit_results_attempt_scope` from
   `(item_id,attempt,batch_id,course_id,user_id)` to the matching unique key in
   `note_generation_attempts`.
3. Make task-result reason validity bidirectional: `covered` requires SQL NULL; `skipped` and
   `failed` require non-blank `reason_code`.
4. Make version-unit reason validity bidirectional: `pending` and `covered` require SQL NULL;
   `skipped` and `failed` require non-blank `reason_code`.
5. Add `ck_note_generation_items_phase` for NULL or one of the seven P1 phase values listed in
   Current state.
6. Add `ck_note_version_coverage_units_type` matching the existing task-unit values:
   `slide`, `pdf_section`, and `pdf_page_window`.

Do not add FKs from immutable source snapshots to mutable revision/chunk rows. Their documented
retention boundary deliberately allows ingestion cleanup while historical Note facts remain.

Use `20260722_0008_note_batch_commands.py` as the source shape only after inspecting its diff. Keep
`down_revision = "7102eb21ee91"`. It may add only batch command/title/section/target
columns/checks/index and output `note_version` backfill/FK/unique metadata. Make one required narrow
correction: `command_kind` and `section_path` may use server defaults while adding non-null columns,
but 0008 must remove both temporary server defaults before `upgrade()` returns. The ORM intentionally
uses client-side defaults, so the head database must report SQL NULL for both `column_default`
values. Do not add ORM `server_default` merely to mirror the checkpoint mistake. If 0008 contains
any export, cleanup, repository, or route behavior, STOP because the source drifted.

Verify the static migration boundary:

```bash
uv run python -m py_compile \
  services/api/alembic/versions/7102eb21ee91_note_workflow_expand.py \
  services/api/alembic/versions/20260722_0008_note_batch_commands.py
uv run alembic -c services/api/alembic.ini heads
! rg -n 'note_exports|note_export_attempts|storage_cleanup_tasks|note-export|stored_objects' \
  services/api/alembic/versions/7102eb21ee91_note_workflow_expand.py \
  services/api/alembic/versions/20260722_0008_note_batch_commands.py
```

Expected: compile succeeds, Alembic prints exactly `20260722_0008 (head)`, and the forbidden scan
has no match.

### Step 2: Extract the matching ORM without deferred classes

Create `note_versions.py` from the seven allowed checkpoint classes. Create `note_workflow.py` from
the ten allowed task classes, retaining the post-0008 batch fields and output `note_version`. Do not
copy the three export/cleanup classes or list them in `__all__`.

Apply the same four strengthened invariant groups from Step 1 to ORM `__table_args__`: the two
coverage-result FKs, the two reason CHECKs, item phase CHECK, and version-unit type CHECK. Constraint
names and ordered column lists must match the migrations byte-for-byte at the semantic level.

In `models/__init__.py`, add only the seven version and ten task model imports/exports. Preserve all
main conversation imports; do not restore the whole checkpoint registry blindly.

Verify model registration and the forbidden boundary:

```bash
uv run python - <<'PY'
from study_agent.infrastructure.db import models as _models  # noqa: F401
from study_agent.infrastructure.db.base import Base

expected = {
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
forbidden = {"note_exports", "note_export_attempts", "storage_cleanup_tasks"}
assert expected <= set(Base.metadata.tables)
assert forbidden.isdisjoint(Base.metadata.tables)

required_constraints = {
    "fk_note_coverage_unit_results_item_input_scope",
    "fk_note_coverage_unit_results_attempt_scope",
    "ck_note_coverage_unit_results_reason",
    "ck_note_version_coverage_units_reason",
    "ck_note_generation_items_phase",
    "ck_note_version_coverage_units_type",
}
actual_constraints = {
    constraint.name
    for table_name in expected
    for constraint in Base.metadata.tables[table_name].constraints
    if constraint.name is not None
}
assert required_constraints <= actual_constraints
PY
! rg -n 'NoteExport|StorageCleanup|note_exports|note_export_attempts|storage_cleanup_tasks' \
  services/api/src/study_agent/infrastructure/db/models/note_versions.py \
  services/api/src/study_agent/infrastructure/db/models/note_workflow.py \
  services/api/src/study_agent/infrastructure/db/models/__init__.py
```

Expected: metadata assertions pass and the forbidden scan has no match.

### Step 3: Replace weak schema checks with behavioral PostgreSQL tests

Use `test_note_workflow_constraints.py` as a PostgreSQL integration module, not a constraint-name
smoke test. Add small SQLAlchemy/raw-SQL seed helpers and cover all of these cases:

1. Head is `20260722_0008`; all 17 selected tables exist; the three deferred tables do not exist;
   the six strengthened constraint names exist; `note_generation_batches` has the 0008 columns,
   `note_generation_outputs.note_version` is non-null, and `command_kind`/`section_path` have no
   persistent database default.
2. Same IDs combined with a different user/course/batch/note/version are rejected by representative
   composite FKs. Include batch retry scope, input batch/document scope, item batch scope, version
   Note scope, 0008 target version scope, and output exact-version scope.
3. A coverage result is rejected when its `(item,input)` membership is absent even if both rows are
   in the same batch.
4. A coverage result is rejected when its attempt row is absent; it succeeds only after both the
   item-input membership and the exact attempt row exist.
5. Task and version coverage reason rules accept covered/pending without a reason and skipped/failed
   with a non-blank reason; reject the inverse combinations.
6. NULL and every P1 phase value are accepted; an unknown phase is rejected by PostgreSQL.
7. `0008` create/retry/regeneration target combinations, merged/per-document title rules, target
   hash/version checks, and output exact-version uniqueness/FK are enforced.
8. For every selected table, compare canonical live-PostgreSQL and `Base.metadata` maps:
   column name to PostgreSQL type/length/precision/scale, nullability, and normalized server-default
   value; ordered primary-key columns; UNIQUE constraint name to ordered columns; CHECK constraint
   name to normalized PostgreSQL expression; FK name to ordered local columns, referred schema/table,
   ordered referred columns, `ondelete`, `onupdate`, deferrability, and initially mode; and explicit
   index name to ordered columns/expressions, uniqueness, and PostgreSQL predicate. Filter inspector
   indexes carrying `duplicates_constraint` so PK/UNIQUE backing indexes are not double-counted.
   The CHECK canonicalizer must also equate PostgreSQL's reflected
   `column = ANY (ARRAY[...])` form with the metadata `column IN (...)` form and normalize casts on
   array/literal members, in addition to redundant outer parentheses and whitespace. Do not weaken
   any operator or literal while canonicalizing. Behavioral positive/negative writes remain the
   second proof for each load-bearing CHECK.

Each negative case must assert `IntegrityError` or the specific migration error. Do not merely query
`pg_constraint`. Keep the inventory test as a separate fast diagnostic, but behavioral violations
are the completion evidence.

In `test_migrations.py`, add a scoped migration regression that:

- starts at 0007 and inserts a valid legacy synchronous Note;
- upgrades through 7102 and 0008, asserting the Note row is byte-for-byte unchanged;
- downgrades to 0007, asserting selected new tables are gone and the legacy Note remains;
- upgrades back to head in `finally` so the session fixture can clean up safely.

In `test_answering_migration.py`, change only the head expectation from `20260721_0007` to
`20260722_0008`. Preserve the conversation/backfill assertions.

Format only the eight in-scope Python files:

```bash
uv run ruff format \
  services/api/alembic/versions/20260722_0008_note_batch_commands.py \
  services/api/alembic/versions/7102eb21ee91_note_workflow_expand.py \
  services/api/src/study_agent/infrastructure/db/models/__init__.py \
  services/api/src/study_agent/infrastructure/db/models/note_versions.py \
  services/api/src/study_agent/infrastructure/db/models/note_workflow.py \
  services/api/tests/integration/test_answering_migration.py \
  services/api/tests/integration/test_migrations.py \
  services/api/tests/integration/test_note_workflow_constraints.py
```

Then run the focused tests from the command table.
Expected: all focused tests pass against disposable PostgreSQL; no export/core/storage fixture is
needed.

### Step 4: Prove migration/ORM parity on PostgreSQL

Re-run the Step 0 database guard in the same shell, then run the focused DB command from the table.
The exhaustive live-schema/`Base.metadata` test from Step 3 must pass for all 17 tables, including
server-default presence. Then run Alembic's additional metadata comparison against the upgraded
disposable database:

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" uv run python - <<'PY'
import asyncio
import os

from alembic import command

from study_agent.infrastructure.db.migrations import _alembic_config, upgrade_database

database_url = os.environ["TEST_DATABASE_URL"]
asyncio.run(upgrade_database(database_url))
command.check(_alembic_config(database_url))
PY
```

Expected: the exhaustive parity test passes and Alembic prints
`No new upgrade operations detected.` Alembic's current environment does not enable
`compare_server_default`, so its output does not replace the explicit server-default comparison.
If Alembic proposes task/version operations, fix only the in-scope migration/ORM mismatch. If it
proposes a change outside this plan's tables, STOP and report the pre-existing drift rather than
editing unrelated models/migrations.

### Step 5: Run regressions and prove no public contract changed

Re-run the Step 0 database guard in the same shell before the full Python suite. Run the contract,
full Python, Web, typecheck, lint, and existing E2E commands from the command table. Regenerate API
artifacts once, then prove they are unchanged from base:

```bash
npm run generate:api
git diff --exit-code "$BASE_SHA" -- \
  packages/contracts/openapi/openapi.json \
  apps/web/src/api/generated/schema.ts \
  packages/contracts/python
```

Expected: every gate passes and the public contract diff is empty. If generation exposes any
task/version schema or route, locate the accidental FastAPI registration and STOP; do not hand-edit
generated files.

Run static and hygiene gates:

```bash
MYPYPATH=packages/contracts/python/src:services/api/src:services/worker/src \
  uv run mypy -p study_contracts -p study_agent -p study_worker
uv run ruff format --check .
uv run ruff check .
git diff --check "$BASE_SHA"
```

Expected: mypy reports success; Ruff and diff checks exit 0 with no errors.

### Step 6: Enforce the exact scope and forbidden boundary

First prove all behavior and deferred paths are unchanged:

```bash
git diff --exit-code "$BASE_SHA" -- \
  services/api/src/study_agent/infrastructure/db/models/core.py \
  services/api/src/study_agent/providers \
  services/api/src/study_agent/modules/notes \
  services/api/src/study_agent/api/routers \
  services/api/src/study_agent/main.py \
  packages/contracts \
  apps/web \
  tests/e2e \
  .claude/planning \
  .idea
```

Expected: no output. Compare tracked plus untracked paths to the exact eight-file allowlist:

```bash
ACTUAL_PATHS=$(mktemp)
EXPECTED_PATHS=$(mktemp)
{
  git diff --name-only "$BASE_SHA"
  git ls-files --others --exclude-standard
} | sort -u > "$ACTUAL_PATHS"
cat > "$EXPECTED_PATHS" <<'EOF'
services/api/alembic/versions/20260722_0008_note_batch_commands.py
services/api/alembic/versions/7102eb21ee91_note_workflow_expand.py
services/api/src/study_agent/infrastructure/db/models/__init__.py
services/api/src/study_agent/infrastructure/db/models/note_versions.py
services/api/src/study_agent/infrastructure/db/models/note_workflow.py
services/api/tests/integration/test_answering_migration.py
services/api/tests/integration/test_migrations.py
services/api/tests/integration/test_note_workflow_constraints.py
EOF
sort -u -o "$EXPECTED_PATHS" "$EXPECTED_PATHS"
diff -u "$EXPECTED_PATHS" "$ACTUAL_PATHS"
rm "$EXPECTED_PATHS" "$ACTUAL_PATHS"
```

Expected: `diff` exits 0. Final forbidden scan:

```bash
! rg -n \
  'NoteExport|StorageCleanup|note_exports|note_export_attempts|storage_cleanup_tasks|note-export' \
  services/api/alembic/versions/7102eb21ee91_note_workflow_expand.py \
  services/api/alembic/versions/20260722_0008_note_batch_commands.py \
  services/api/src/study_agent/infrastructure/db/models/note_versions.py \
  services/api/src/study_agent/infrastructure/db/models/note_workflow.py \
  services/api/src/study_agent/infrastructure/db/models/__init__.py
```

Expected: no match.

### Step 7: Stage explicitly and create one commit

Do not use `git add .` or `git add -A`. Stage exactly the eight allowlisted paths, then inspect:

```bash
git add \
  services/api/alembic/versions/20260722_0008_note_batch_commands.py \
  services/api/alembic/versions/7102eb21ee91_note_workflow_expand.py \
  services/api/src/study_agent/infrastructure/db/models/__init__.py \
  services/api/src/study_agent/infrastructure/db/models/note_versions.py \
  services/api/src/study_agent/infrastructure/db/models/note_workflow.py \
  services/api/tests/integration/test_answering_migration.py \
  services/api/tests/integration/test_migrations.py \
  services/api/tests/integration/test_note_workflow_constraints.py
git diff --cached --name-only
git diff --cached --check
git status --short
```

Expected: exactly eight staged paths and no unstaged slice changes. Commit and prove shape:

```bash
git commit -m "feat: add note workflow data foundation"
test "$(git rev-list --count "$BASE_SHA"..HEAD)" = 1
test "$(git merge-base "$BASE_SHA" HEAD)" = "$BASE_SHA"
test -z "$(git status --porcelain)"
git show --stat --oneline --decorate HEAD
```

Expected: one clean local commit directly above `main@d8fe9d0`, with no push/upstream.

## Test plan

- Migration topology: one head at 0008; upgrade from 0007, downgrade to 0007, and re-upgrade.
- Legacy compatibility: a synchronous legacy Note survives the expand/0008 round trip unchanged;
  existing Note integration tests remain green.
- Inventory: exactly the selected task/version tables and 0008 columns exist; export/cleanup tables
  and StoredObject purpose changes do not.
- Scope integrity: negative PostgreSQL cases for cross course/user/batch/item/Note/version links.
- Coverage integrity: results require real item-input membership and the exact attempt row.
- Contract parity: coverage reason, unit type, item phase, command/target, terminal-time, and exact
  output-version CHECK/FK behavior is tested through accepted and rejected writes.
- ORM parity: Alembic `check` reports no metadata operations after upgrading to head.
- Public surface: OpenAPI and generated TypeScript reproduce with no diff; no route is added.
- Repository regression: full non-live Python, Web Vitest/typecheck/lint, existing E2E, mypy, Ruff,
  and diff hygiene pass.

## Done criteria

- [x] Target branch starts clean from exact `origin/main@d8fe9d0`; checkpoint remains untouched.
- [x] Exactly the eight allowlisted paths differ from base, including untracked-file accounting.
- [x] Alembic has the linear chain `0007 -> 7102 -> 0008` and exactly one head at 0008.
- [x] 7102 creates only 10 task plus 7 version/source tables; it does not alter StoredObject or
      create export/cleanup tables.
- [x] ORM registers exactly the selected 17 new tables and maps the complete post-0008 columns.
- [x] Migration and ORM contain matching membership/attempt FKs, reason/unit/phase CHECKs, and
      exhaustive live-schema parity passes; both temporary 0008 defaults are absent; Alembic
      `check` reports no pending operations.
- [x] PostgreSQL behavioral tests reject cross-scope, missing membership, missing attempt, invalid
      reason/type/phase, invalid command target, and wrong output version writes.
- [x] 0007/head round trip preserves legacy Notes and restores head in cleanup/finally paths.
- [x] Existing synchronous Notes, query conversation migrations, and P1 contract tests pass.
- [x] Full non-live Python, Web tests/typecheck/lint, 20 existing E2E cases, mypy, Ruff, generated
      artifact equality, and diff hygiene pass.
- [x] One clean local commit exists; nothing was pushed, merged, or added to AIWF documents.

## STOP conditions

Stop and report the exact command/path/dependency; do not improvise if any is true:

- Base/source/merge-base differs from the pinned SHAs, or target branch/worktree is not clean.
- Dependency bootstrap fails or changes `uv.lock`/`package-lock.json`.
- `TEST_DATABASE_URL` is absent, unparseable, or does not name a disposable database containing
  `test`; no integration test may run before this guard passes.
- Pruned 7102 cannot upgrade/downgrade without StoredObject, export, or cleanup schema.
- 0008 cannot run after the selected 7102 tables without importing P3/P4 repository or route code.
- A selected whole-file source contains export/cleanup behavior or imports after source inspection.
- SQLAlchemy/Alembic parity requires registering export/cleanup models or touching an existing
  out-of-scope model/migration.
- Strengthened coverage-result FKs cannot be satisfied without changing task/version repository
  behavior. Report the write ordering; do not pull the repository into this slice.
- The landed P1 contract's reason, unit type, phase, command, or target invariant differs from the
  Current state values.
- OpenAPI or generated TypeScript changes after clean generation.
- A test failure requires AST/canonical code, version repository/backfill/lifecycle, state machine,
  task repository, batch service/router, runner, Web UI, DOCX/export/cleanup, or exact ETA.
- Any PostgreSQL target fails the `test`-database name guard.
- Alembic reports multiple heads or a head other than `20260722_0008`.
- The final path comparison or cached diff contains any path outside the eight-file allowlist.

## Maintenance notes

- This slice proves dormant persistence only. Do not describe it as a persistent-task API or a
  working Note-generation workflow.
- 0008 is included to keep the selected ORM and already-landed P1 contract persistence shape
  coherent; its retry/regeneration fields remain behaviorally dormant.
- After this migration lands, deferred export/cleanup schema must use a new forward migration. Do
  not amend 7102 later on a shared branch.
- The next dependency should establish immutable version writes/backfill/history before the batch
  control plane publishes exact output versions. Neither follow-up is planned in this file.
- Immutable version/source rows deliberately avoid FKs to mutable ingestion revision/chunk rows.
  Preserve that retention boundary in later reviews.
- Generated OpenAPI/TypeScript are reviewed structurally and for equality, never by file size.
