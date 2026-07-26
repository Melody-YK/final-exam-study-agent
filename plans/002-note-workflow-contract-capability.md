# Plan 002: Isolate note-workflow contracts and closed capability gates

> **Executor instructions**: Read this plan completely before changing code. Execute the steps in
> order and run every verification command. The checkpoint is a mixed P1-P4 snapshot on a sibling
> history; file existence is not proof that a whole-file restore is safe. Use the extraction table
> below, preserve the conversation contract already on `main`, and stop rather than importing a
> migration, ORM model, route, repository, runner, or Notes UI to make this slice pass.
>
> **Pinned refs**: Base is `origin/main@dd239e91adcecc770c5c32d5dce754e73b2b2feb` and source is
> `codex/workflow-checkpoint@7c08333bb22ca7b7046c2f82ddced5794e8fb78a`. Their merge-base is
> `856cb1d5b39241e6591b0396a161764649dc0832`; they are sibling histories. Never rebase, merge, or
> cherry-pick the checkpoint. Read blobs with `git show` and extract only this plan's allowlist.
>
> **Contract boundary**: `study_contracts.note_workflow` is an intentionally dormant P1 contract
> freeze. It may describe future batch, coverage, AST, ETA, and export resources, but no such route
> may enter OpenAPI in this slice. Historical Note response models in `study_contracts.notes`, the
> P3 AST/version implementation, and the P4 batch control plane remain excluded.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MED
- **Depends on**: Plan 001 merged as `main@dd239e9`
- **Category**: contracts / configuration / API capability / security / tests
- **Planned at**: `dd239e91adcecc770c5c32d5dce754e73b2b2feb`, 2026-07-22
- **Target branch**: `codex/note-workflow-contract-capability`
- **Target worktree**: `/Users/melody/Desktop/all_for_ending_week-note-workflow-contract-capability`
- **Target commit shape**: one independently verifiable local commit; do not push or open a PR
- **Status**: DONE: verified at `44660ad` on 2026-07-23

## Why this matters

The checkpoint records P1 as complete, but its current files also contain later P3/P4 work. This
slice must recover the useful P1 boundary on top of the now-merged conversation work: versioned
Python request/snapshot contracts, stable public errors, fail-closed settings, and a nested runtime
capability. The legacy synchronous Notes API must remain unchanged, and OpenAPI/TypeScript must
describe only the capability that the clean app actually exposes.

This is a prerequisite, not the demonstrable note workflow itself. It creates no task, stores no
batch, generates no Note, and adds no preview UI.

## Current state

- `origin/main@dd239e9` contains Plan 001, including migration 0007, conversation endpoints, Web QA
  continuity, and its generated OpenAPI/TypeScript contract.
- The checkpoint was committed from a mixed worktree and is not descended from the new `main`.
  Direct whole-file replacement can silently remove conversation assertions or import P3/P4 code.
- AIWF P1 explicitly owns contract/config/error/capability freeze and default-off behavior. It
  explicitly defers asynchronous route registration to P4.
- `packages/contracts/python/src/study_contracts/note_workflow.py` is self-contained and may be
  extracted as the dormant P1 contract. It imports only shared document contract primitives.
- `packages/contracts/python/src/study_contracts/notes.py` is not P1-safe in the checkpoint: its
  delta adds `CanonicalNoteVersionV1`, source overlays, and historical version snapshots owned by
  P3. It must remain byte-identical to `origin/main`.
- The checkpoint's `test_note_workflow_contracts.py` is mixed: its create/batch/ETA/coverage/AST
  tests are P1, while `NoteVersionSource` redaction coverage is P3.
- The checkpoint's `test_openapi.py` replaces the Plan 001 conversation guard and requires a P3
  historical-version route. It must be rebuilt from the `origin/main` file, not restored.
- The checkpoint OpenAPI and generated TypeScript contain note-batch and historical-version
  routes. Both generated artifacts must be reproduced from the clean P1 app.
- The capability in this slice means configuration readiness. It does not prove a live trusted
  Note Runner, provider latency, renderer isolation, or production readiness.
- The checkpoint detail sanitizer misses a synthetic local path written as `path=/...`; its test
  combines several sensitive markers, so an earlier marker masks that miss. The slice must repair
  the sanitizer and test every sensitive category independently.
- The checkpoint settings tests do not independently prove that runner, chat, and embedding are
  each required. They also miss several cross-field negative cases.

## Scope

### Whole-file source extraction allowed

These paths have no Plan 001 drift between `856cb1d` and `dd239e9`, and their checkpoint deltas were
reviewed as P1-only. Restore them from `SOURCE_SHA`, then make only the test-quality/sanitizer
corrections specified later:

- `packages/contracts/python/src/study_contracts/note_workflow.py`
  - All enums and Pydantic contracts: create request union, batch/item/input snapshots, coverage,
    ETA, draft/AST, `NoteVersionCoverage`, and export snapshot.
  - `NoteBatchCommandKind` and exact-version command fields remain dormant Python contract only.
- `services/api/src/study_agent/api/errors.py`
  - Note `ProblemCode` members, `ApiProblem.retry_after_ms`, public detail sanitization, and handler
    projection.
- `services/api/src/study_agent/api/routers/workspace.py`
  - `NoteWorkflowCapabilityResponse`, `RuntimeCapabilitiesResponse.note_workflow`, and the
    generation/export/ETA configuration-readiness projection.
- `services/api/src/study_agent/api/schemas/__init__.py`
- `services/api/src/study_agent/api/schemas/note_workflow.py`
  - Dormant HTTP schema facade only; no router imports it in this slice.
- `services/api/src/study_agent/config.py`
  - Default-off `note_*` settings, validation limits/relationships, and
    `note_workflow_configured` / `note_docx_configured`.
- `services/api/tests/unit/test_config.py`
- `services/api/tests/unit/test_errors_and_redaction.py`
- `apps/web/src/test/render.tsx`
  - Only the typed `availableCapabilities.note_workflow` fixture is new.

Before each restore, run `git diff BASE_SHA SOURCE_SHA -- <path>` and verify that the source delta
matches the symbols above. If it imports any note ORM, repository, router, state machine, runner, or
historical Note model, do not restore it; STOP and report source drift.

### Mixed or authored files: apply only the listed symbols/hunks

| File | Allowed change | Explicitly reject |
|---|---|---|
| `packages/contracts/python/src/study_contracts/__init__.py` | Import/export every symbol from `study_contracts.note_workflow` present in the source facade | `CanonicalNoteVersionV1`, `NoteSourceOverlay`, `NoteVersionSnapshot`, `NoteVersionSource`, or changing the baseline `Note`/`NoteSource` import |
| `packages/contracts/python/tests/test_note_workflow_contracts.py` | Recreate the P1 tests at source lines 26-206 and add the validator cases in Step 2 | `NoteVersionSource` import/test or any P3 implementation fixture |
| `services/api/tests/contract/test_openapi.py` | Preserve all `origin/main` conversation assertions and add a separate capability/legacy-Notes test | Checkpoint historical-version/ETag assertions or replacement of the conversation test |
| `services/api/tests/integration/test_workspace_api.py` | Add default-off `note_workflow` response assertions beside the existing capability request | Replacing the file, conversation fixture changes, note ORM, or unrelated Lab changes |
| `services/api/tests/integration/test_worker_presence.py` | Configure all note gates in the existing capability test and assert the fully configured projection without external calls | Note runner presence, provider calls, ORM, batch routes, or changing parse-worker semantics |
| `tests/e2e/mockApi.ts` | Add the required nested `note_workflow` object to the existing `/capabilities` response | Note routes, Notes UI behavior, or weakening existing E2E assertions |

Never use `git restore`, `git checkout`, or whole-file copy for a mixed file. Start from the
`origin/main` version and apply only the allowlisted edits with a patch editor. Inspect each file's
diff immediately after editing.

### Generate on the clean branch

- `packages/contracts/openapi/openapi.json`
- `apps/web/src/api/generated/schema.ts`

Do not copy either file from the checkpoint and do not hand-edit generated text. The clean app is
the only source of truth.

### Exact changed-path allowlist

The final feature commit may contain only these 17 paths:

1. `apps/web/src/api/generated/schema.ts`
2. `apps/web/src/test/render.tsx`
3. `packages/contracts/openapi/openapi.json`
4. `packages/contracts/python/src/study_contracts/__init__.py`
5. `packages/contracts/python/src/study_contracts/note_workflow.py`
6. `packages/contracts/python/tests/test_note_workflow_contracts.py`
7. `services/api/src/study_agent/api/errors.py`
8. `services/api/src/study_agent/api/routers/workspace.py`
9. `services/api/src/study_agent/api/schemas/__init__.py`
10. `services/api/src/study_agent/api/schemas/note_workflow.py`
11. `services/api/src/study_agent/config.py`
12. `services/api/tests/contract/test_openapi.py`
13. `services/api/tests/integration/test_worker_presence.py`
14. `services/api/tests/integration/test_workspace_api.py`
15. `services/api/tests/unit/test_config.py`
16. `services/api/tests/unit/test_errors_and_redaction.py`
17. `tests/e2e/mockApi.ts`

### Explicitly out of scope

- Every Alembic file, including `7102eb21ee91_note_workflow_expand.py` and
  `20260722_0008_note_batch_commands.py`; Alembic head must remain 0007.
- `packages/contracts/python/src/study_contracts/notes.py` and historical Note response models.
- `services/api/src/study_agent/main.py`, `api/routers/notes.py`, and
  `api/routers/note_batches.py`.
- All note-workflow ORM, note-version ORM, storage-purpose changes, and model registration.
- All `services/api/src/study_agent/modules/notes/**`: AST parser, canonicalization, backfill,
  lifecycle, version repository, batch service, task repository, state machine, and runner.
- All note migration/API integration tests, including `test_notes.py` changes,
  `test_note_versions.py`, `test_note_batches.py`, and `test_note_batch_api.py`. Existing
  `test_notes.py` is verification-only.
- `apps/web/src/api/client.ts`, `apps/web/src/api/types.ts`, workspace context/banner product code,
  `apps/web/src/features/notes/**`, and every QA page file.
- DOCX generation, export lifecycle, cleanup, exact ETA, runtime liveness, external queues,
  production isolation, and cross-platform certification. Default-off contract/config fields are
  not delivery of these deferred capabilities.
- `.idea/**`, AIWF artifacts under `.claude/planning/**`, `plans/**` in the feature commit, and
  unrelated refactors or new product behavior.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Alembic head | `uv run alembic -c services/api/alembic.ini heads` | exactly `20260721_0007 (head)` |
| P1 focused Python | `uv run pytest packages/contracts/python/tests/test_note_workflow_contracts.py services/api/tests/unit/test_config.py services/api/tests/unit/test_errors_and_redaction.py services/api/tests/contract/test_openapi.py -q` | every collected test passes |
| Capability API | `uv run pytest services/api/tests/integration/test_workspace_api.py services/api/tests/integration/test_worker_presence.py services/api/tests/integration/test_notes.py -q` | every collected test passes; no external provider call |
| Full non-live Python | `uv run pytest -m "not live" -q` | every collected test passes; live tests remain deselected |
| Generate API | `npm run generate:api` | exits 0 from the clean app |
| Generated TS check | `npm exec --workspace @study-agent/web openapi-typescript -- ../../packages/contracts/openapi/openapi.json -o src/api/generated/schema.ts --check` | exits 0 only when the committed TS matches generation |
| Web Vitest | `npm test --workspace @study-agent/web` | every collected test passes |
| Web typecheck | `npm run typecheck --workspace @study-agent/web` | exits 0 |
| Web lint | `npm run lint --workspace @study-agent/web` | exits 0 with no warning |
| Capability-mock E2E | `npm run test:e2e -- tests/e2e/qa-notes.spec.ts tests/e2e/library.spec.ts` | 20 passed across four projects |
| Python typecheck | `MYPYPATH=packages/contracts/python/src:services/api/src:services/worker/src uv run mypy -p study_contracts -p study_agent -p study_worker` | `Success: no issues found` |
| Python format | `uv run ruff format --check .` | exits 0 |
| Python lint | `uv run ruff check .` | exits 0 |
| Diff hygiene | `git diff --check` | no output |

PostgreSQL commands require `TEST_DATABASE_URL` to identify an explicitly disposable test
database. Never run the integration or full suite against a development or user database.

## Git workflow

- Keep the mixed checkpoint worktree untouched.
- Create the target branch from the pinned new-main commit in a separate sibling worktree.
- Do not merge or cherry-pick the checkpoint; it is only a blob source.
- Commit only after every gate passes. Use one commit:
  `feat: add note workflow contracts and capability gates`.
- Do not push, merge, open a PR, or update AIWF status artifacts during execution.
- This plan and its index remain in the original checkpoint worktree and are not part of the
  feature commit.

## Steps

### Step 0: Verify immutable refs and create the clean worktree

Run from `/Users/melody/Desktop/all_for_ending_week`:

```bash
git fetch --prune origin
BASE_SHA=$(git rev-parse origin/main)
SOURCE_SHA=$(git rev-parse --verify refs/heads/codex/workflow-checkpoint^{commit})
OLD_BASE_SHA=856cb1d5b39241e6591b0396a161764649dc0832
test "$BASE_SHA" = dd239e91adcecc770c5c32d5dce754e73b2b2feb
test "$SOURCE_SHA" = 7c08333bb22ca7b7046c2f82ddced5794e8fb78a
test "$(git merge-base "$BASE_SHA" "$SOURCE_SHA")" = "$OLD_BASE_SHA"
git show "$SOURCE_SHA:packages/contracts/python/src/study_contracts/note_workflow.py" \
  | rg 'class NoteBatchSnapshot|class NoteExportSnapshot|class StructuredNoteDraftV1'
git show "$SOURCE_SHA:services/api/src/study_agent/api/routers/workspace.py" \
  | rg 'class NoteWorkflowCapabilityResponse|note_workflow=NoteWorkflowCapabilityResponse'
git branch --list codex/note-workflow-contract-capability
```

Expected: both refs equal the pinned SHAs, merge-base equals `856cb1d`, all source probes match,
and the branch command prints nothing. The sibling-history result is intentional. If any value
differs, STOP for plan reconciliation rather than silently using newer refs.

Confirm the target path is unused, then create the branch:

```bash
TARGET_WORKTREE=/Users/melody/Desktop/all_for_ending_week-note-workflow-contract-capability
test ! -e "$TARGET_WORKTREE"
git worktree add -b codex/note-workflow-contract-capability "$TARGET_WORKTREE" "$BASE_SHA"
git -C "$TARGET_WORKTREE" status --short --branch
git -C "$TARGET_WORKTREE" rev-parse HEAD
```

Expected: a clean `codex/note-workflow-contract-capability` worktree at `BASE_SHA`. Re-export
`BASE_SHA`, `SOURCE_SHA`, and `OLD_BASE_SHA` in every new shell. Enter the target worktree and use
the repository's standard dependency bootstrap; ignored environments are not carried into a new
worktree:

```bash
cd "$TARGET_WORKTREE"
uv sync --all-packages
npm install
git diff --exit-code "$BASE_SHA" -- uv.lock package-lock.json
git status --short --branch
```

Expected: Python and Node dependencies install successfully, both lockfiles remain byte-identical
to base, and status is still clean. `.venv` and `node_modules` are ignored local dependencies. If
the standard bootstrap changes a lockfile, STOP and report toolchain/dependency drift; do not add a
lockfile update to this feature slice.

### Step 1: Reconfirm whole-file and mixed-file classification

In the target worktree, first prove which in-scope paths changed when Plan 001 landed:

```bash
git diff --name-status "$OLD_BASE_SHA" "$BASE_SHA" -- \
  packages/contracts/python \
  packages/contracts/openapi/openapi.json \
  services/api/src/study_agent/api/errors.py \
  services/api/src/study_agent/api/routers/workspace.py \
  services/api/src/study_agent/api/schemas \
  services/api/src/study_agent/config.py \
  services/api/tests/contract/test_openapi.py \
  services/api/tests/integration/test_workspace_api.py \
  services/api/tests/integration/test_worker_presence.py \
  services/api/tests/unit/test_config.py \
  services/api/tests/unit/test_errors_and_redaction.py \
  apps/web/src/api/generated/schema.ts \
  apps/web/src/test/render.tsx \
  tests/e2e/mockApi.ts
```

Expected: exactly five paths appear: the two generated artifacts, `test_openapi.py`,
`test_workspace_api.py`, and `tests/e2e/mockApi.ts`. These are Plan 001 conversation changes and
must be preserved. If any whole-file extraction path appears, STOP and reclassify it before
editing.

Inspect the checkpoint deltas:

```bash
git diff --name-status "$BASE_SHA" "$SOURCE_SHA" -- \
  packages/contracts/python \
  services/api/src/study_agent/api \
  services/api/src/study_agent/config.py \
  services/api/tests/contract/test_openapi.py \
  services/api/tests/integration/test_workspace_api.py \
  services/api/tests/unit \
  packages/contracts/openapi/openapi.json \
  apps/web/src/api/generated/schema.ts \
  apps/web/src/test/render.tsx
git diff --unified=5 "$BASE_SHA" "$SOURCE_SHA" -- \
  packages/contracts/python/src/study_contracts/__init__.py \
  packages/contracts/python/src/study_contracts/notes.py \
  packages/contracts/python/tests/test_note_workflow_contracts.py \
  services/api/tests/contract/test_openapi.py
```

Expected: the mixed files show the P3 symbols called out in Scope; generated files show broad
P3/P4 pollution. Do not start extraction until the reviewed classification still matches.

### Step 2: Extract the dormant Python contract and build mutation-resistant tests

Restore only the self-contained contract file:

```bash
git restore --source "$SOURCE_SHA" -- \
  packages/contracts/python/src/study_contracts/note_workflow.py
```

Manually patch `study_contracts/__init__.py` with the checkpoint's `note_workflow` import and
`__all__` entries. Keep its baseline line equivalent to:

```python
from study_contracts.notes import Note, NoteSource
```

Do not import or export `CanonicalNoteVersionV1`, `NoteSourceOverlay`, `NoteVersionSnapshot`, or
`NoteVersionSource`.

Create `test_note_workflow_contracts.py` from the P1 source tests only, then strengthen it so the
following production validators cannot be removed without a failure:

1. Discriminated create requests normalize optional text and reject empty selection, duplicate
   document IDs, extra fields from the wrong mode, and overlong/invalid values.
2. `CoverageUnitSnapshot.reason_must_match_status` rejects missing reasons for skipped/failed and
   unexpected reasons for pending/covered.
3. `NoteItemSnapshot.eta_must_have_exactly_one_representation` rejects both/neither ETA forms and
   requires terminal items to use the terminal unavailable reason.
4. `NoteBatchSnapshot` validates timezone-aware timestamps, completed/total counts, terminal
   timestamps, default `command_kind=create`, mode/title fields, retry-parent commands, and the
   complete exact-version regeneration target.
5. Draft/AST tests reject duplicate node/claim/citation/unit IDs and unknown citation or coverage
   references; the positive fixture still validates.
6. `NoteVersionCoverage` enforces generated-version/basis consistency.
7. `NoteExportSnapshot` rejects a naive expiry timestamp and accepts a timezone-aware one.

Use synthetic IDs/content only. Do not import any P3 parser, canonicalizer, version model, or
repository.

Verify the contract boundary:

```bash
git diff --exit-code "$BASE_SHA" -- \
  packages/contracts/python/src/study_contracts/notes.py
! rg -n 'CanonicalNoteVersionV1|NoteSourceOverlay|NoteVersionSnapshot|NoteVersionSource' \
  packages/contracts/python/src/study_contracts/__init__.py \
  packages/contracts/python/tests/test_note_workflow_contracts.py
uv run pytest packages/contracts/python/tests/test_note_workflow_contracts.py -q
```

Expected: `notes.py` has no diff, the forbidden-symbol search has no match, and all new contract
tests pass. If the contract cannot import or validate without `notes.py`, ORM, migration, or P3/P4
code, STOP.

### Step 3: Extract config, errors, schema facade, and capability projection

Restore the reviewed P1-only files:

```bash
git restore --source "$SOURCE_SHA" -- \
  services/api/src/study_agent/api/errors.py \
  services/api/src/study_agent/api/routers/workspace.py \
  services/api/src/study_agent/api/schemas/__init__.py \
  services/api/src/study_agent/api/schemas/note_workflow.py \
  services/api/src/study_agent/config.py \
  services/api/tests/unit/test_config.py \
  services/api/tests/unit/test_errors_and_redaction.py
```

Make these bounded corrections inside the restored files:

- In `errors.py`, make `_SENSITIVE_DETAIL` detect synthetic Unix and Windows absolute paths when
  preceded by a field delimiter such as `path=` or a quote, not only at start/after whitespace.
  Preserve ordinary actionable detail and API paths that are not local filesystem paths.
- In `test_errors_and_redaction.py`, parameterize each sensitive marker separately through
  `sanitize_problem_detail` or `api_problem_handler`: credential markers, request/response body,
  prompt, object key, `s3://`, `file://`, a synthetic Unix local path after `path=`, a synthetic
  Windows local path after `path=`, and an overlong detail. Add a separate safe-detail case and
  assert `retry_after_ms` projection. Never use a real workstation path or secret.
- Freeze the complete set of new note `ProblemCode` values rather than sampling only three.
- In `test_config.py`, independently omit runner, chat key, and embedding key while all other
  workflow prerequisites are present. Each case must fail closed for the missing prerequisite.
- Add negative cases for coverage-unit limit below document limit, dedup retention below event
  retention, numeric ETA without workflow, and each DOCX prerequisite. Keep a fully configured
  positive case proving `note_workflow_configured` and `note_docx_configured`.

Manually amend the two existing API tests:

- `test_workspace_api.py`: beside the current `/api/v1/capabilities` assertions, assert default
  `note_workflow.enabled` is false; generation/export/ETA are unavailable with stable error codes.
- `test_worker_presence.py`: configure synthetic test provider keys plus all note flags in its
  `Settings`; assert enabled true and all three note capabilities available with null error codes.
  Assert the note projection does not change when the unrelated parse-worker presence expires.
  This proves configuration mapping only and must not call a provider or claim a Note Runner.

Run the non-DB P1 tests now:

```bash
uv run pytest \
  packages/contracts/python/tests/test_note_workflow_contracts.py \
  services/api/tests/unit/test_config.py \
  services/api/tests/unit/test_errors_and_redaction.py -q
```

Expected: every test passes. A missing runner/chat/embedding case must be impossible to construct,
and every sensitive-detail case must return the generic public message while the safe case remains
unchanged.

### Step 4: Merge the OpenAPI test and regenerate only the exposed capability

Keep the three existing `origin/main` tests in `test_openapi.py`, including all conversation and
absence assertions. Add a fourth function that asserts:

- `RuntimeCapabilitiesResponse.note_workflow` is required.
- `NoteWorkflowCapabilityResponse` requires exactly `enabled`, `generation`, `export`, and `eta`.
- Legacy `POST /api/v1/courses/{course_id}/notes` still returns `201` with `NoteResponse`.
- No note-batch or historical-version route exists.
- No public schema starts with `NoteBatch`, `NoteVersion`, `NoteExport`, or `NoteGeneration`.

Do not add the checkpoint's historical-version route/ETag assertion. Generate both artifacts from
the clean app:

```bash
npm run generate:api
uv run pytest services/api/tests/contract/test_openapi.py -q
npm exec --workspace @study-agent/web openapi-typescript -- \
  ../../packages/contracts/openapi/openapi.json -o src/api/generated/schema.ts --check
```

Expected: `4 passed`; generation/check exit 0.

Use a structured comparison against the pinned base rather than reviewing generated size:

```bash
BASE_SHA="$BASE_SHA" uv run python - <<'PY'
import json
import os
import subprocess
from copy import deepcopy
from pathlib import Path

base = json.loads(
    subprocess.check_output(
        [
            "git",
            "show",
            f"{os.environ['BASE_SHA']}:packages/contracts/openapi/openapi.json",
        ],
        text=True,
    )
)
current = json.loads(Path("packages/contracts/openapi/openapi.json").read_text())

assert current["paths"] == base["paths"]
base_schemas = base["components"]["schemas"]
current_schemas = current["components"]["schemas"]
assert set(current_schemas) - set(base_schemas) == {"NoteWorkflowCapabilityResponse"}
assert set(base_schemas) - set(current_schemas) == set()
for name, schema in base_schemas.items():
    if name != "RuntimeCapabilitiesResponse":
        assert current_schemas[name] == schema

before = base_schemas["RuntimeCapabilitiesResponse"]
after = current_schemas["RuntimeCapabilitiesResponse"]
normalized_after = deepcopy(after)
note_property = normalized_after["properties"].pop("note_workflow")
normalized_after["required"].remove("note_workflow")
assert normalized_after == before
assert note_property["$ref"].endswith(
    "/NoteWorkflowCapabilityResponse"
)
note_schema = current_schemas["NoteWorkflowCapabilityResponse"]
assert note_schema["type"] == "object"
assert set(note_schema["properties"]) == {"enabled", "generation", "export", "eta"}
assert note_schema["properties"]["enabled"]["type"] == "boolean"
for field in ("generation", "export", "eta"):
    assert note_schema["properties"][field]["$ref"].endswith("/CapabilityResponse")
assert sorted(note_schema["required"]) == [
    "enabled",
    "eta",
    "export",
    "generation",
]
PY
```

Expected: exit 0. The path map is byte-structurally unchanged; exactly one component is new; the
existing runtime component changes only by the required nested property.

Prove deterministic generation byte-for-byte with temporary copies:

```bash
GEN_COMPARE=$(mktemp -d)
cp packages/contracts/openapi/openapi.json "$GEN_COMPARE/openapi.json"
cp apps/web/src/api/generated/schema.ts "$GEN_COMPARE/schema.ts"
npm run generate:api
cmp "$GEN_COMPARE/openapi.json" packages/contracts/openapi/openapi.json
cmp "$GEN_COMPARE/schema.ts" apps/web/src/api/generated/schema.ts
rm "$GEN_COMPARE/openapi.json" "$GEN_COMPARE/schema.ts"
rmdir "$GEN_COMPARE"
```

Expected: both `cmp` commands exit 0. Then scan only the generated public surface:

```bash
! rg -n \
  'note-batches|versions/\{version\}|CoverageUnit|EtaRange|EtaConfidence|EtaUnavailableReason|MergedNoteBatch|PerDocumentNoteBatch|NoteAst|NoteBatch|NoteContentAst|NoteCoverage|NoteGeneration|NoteInput|NoteItem|NoteSourceOverlay|NoteVersion|NoteExport|StructuredNoteDraft' \
  packages/contracts/openapi/openapi.json \
  apps/web/src/api/generated/schema.ts
```

Expected: no match. `NoteWorkflowCapabilityResponse` is the sole allowed new `Note*` public schema.
If clean generation emits a dormant Python contract, locate the accidental FastAPI registration
and STOP; never delete generated chunks by hand.

### Step 5: Align typed Web and Playwright fixtures without adding UI

Restore the reviewed typed Vitest fixture:

```bash
git restore --source "$SOURCE_SHA" -- apps/web/src/test/render.tsx
```

In `tests/e2e/mockApi.ts`, add only a default-off `note_workflow` object to the existing capability
response. Mirror the API shape: `enabled=false`; generation/export/ETA unavailable; their error
codes are `NOTE_WORKFLOW_DISABLED`, `NOTE_EXPORT_UNAVAILABLE`, and `NOTE_ETA_UNAVAILABLE`.

Do not modify product components, `api/client.ts`, `api/types.ts`, QA/Notes pages, or E2E specs.
Run Web gates:

```bash
npm test --workspace @study-agent/web
npm run typecheck --workspace @study-agent/web
npm run lint --workspace @study-agent/web
npm run test:e2e -- tests/e2e/qa-notes.spec.ts tests/e2e/library.spec.ts
```

Expected: Vitest, typecheck, and lint pass; Playwright reports `20 passed` across Chromium/WebKit
desktop/mobile. Existing query, legacy Note conflict, demo-lab, library, and upload assertions stay
unchanged. If the required generated field forces a product UI change, STOP.

### Step 6: Format only slice Python files and run backend gates

Format the exact Python allowlist:

```bash
uv run ruff format \
  packages/contracts/python/src/study_contracts/__init__.py \
  packages/contracts/python/src/study_contracts/note_workflow.py \
  packages/contracts/python/tests/test_note_workflow_contracts.py \
  services/api/src/study_agent/api/errors.py \
  services/api/src/study_agent/api/routers/workspace.py \
  services/api/src/study_agent/api/schemas/__init__.py \
  services/api/src/study_agent/api/schemas/note_workflow.py \
  services/api/src/study_agent/config.py \
  services/api/tests/contract/test_openapi.py \
  services/api/tests/integration/test_worker_presence.py \
  services/api/tests/integration/test_workspace_api.py \
  services/api/tests/unit/test_config.py \
  services/api/tests/unit/test_errors_and_redaction.py
```

Use an explicitly disposable PostgreSQL database for API/full tests:

```bash
: "${TEST_DATABASE_URL:?Set TEST_DATABASE_URL to a disposable PostgreSQL test database}"
TEST_DATABASE_URL="$TEST_DATABASE_URL" uv run python - <<'PY'
import os

from sqlalchemy.engine import make_url

database_name = make_url(os.environ["TEST_DATABASE_URL"]).database or ""
if "test" not in database_name.lower():
    raise SystemExit("Refusing a database whose database name does not contain 'test'")
PY
uv run alembic -c services/api/alembic.ini heads
uv run pytest \
  packages/contracts/python/tests/test_note_workflow_contracts.py \
  services/api/tests/unit/test_config.py \
  services/api/tests/unit/test_errors_and_redaction.py \
  services/api/tests/contract/test_openapi.py -q
uv run pytest \
  services/api/tests/integration/test_workspace_api.py \
  services/api/tests/integration/test_worker_presence.py \
  services/api/tests/integration/test_notes.py -q
uv run pytest -m "not live" -q
```

Expected: Alembic reports exactly `20260721_0007 (head)` and every test passes. Existing
`test_notes.py` proves the default-off slice did not break synchronous create/read/edit/regenerate;
it must remain unmodified.

Run static and hygiene gates:

```bash
MYPYPATH=packages/contracts/python/src:services/api/src:services/worker/src \
  uv run mypy -p study_contracts -p study_agent -p study_worker
uv run ruff format --check .
uv run ruff check .
npm exec --workspace @study-agent/web openapi-typescript -- \
  ../../packages/contracts/openapi/openapi.json -o src/api/generated/schema.ts --check
git diff --check "$BASE_SHA"
```

Expected: mypy reports success; Ruff and generated checks exit 0; diff check prints nothing.

### Step 7: Enforce the exact scope and forbidden boundaries

First prove that migration and excluded implementation paths are unchanged:

```bash
git diff --exit-code "$BASE_SHA" -- \
  packages/contracts/python/src/study_contracts/notes.py \
  services/api/alembic/versions \
  services/api/src/study_agent/main.py \
  services/api/src/study_agent/api/routers/notes.py \
  services/api/src/study_agent/api/routers/note_batches.py \
  services/api/src/study_agent/infrastructure/db \
  services/api/src/study_agent/modules/notes \
  services/api/tests/integration/test_notes.py \
  apps/web/src/api/client.ts \
  apps/web/src/api/types.ts \
  apps/web/src/features/notes \
  apps/web/src/features/qa \
  .claude/planning \
  .idea
```

Expected: no output. Then mechanically compare tracked plus untracked paths to the 17-path
allowlist:

```bash
ACTUAL_PATHS=$(mktemp)
EXPECTED_PATHS=$(mktemp)
{
  git diff --name-only "$BASE_SHA"
  git ls-files --others --exclude-standard
} | sort -u > "$ACTUAL_PATHS"
cat > "$EXPECTED_PATHS" <<'EOF'
apps/web/src/api/generated/schema.ts
apps/web/src/test/render.tsx
packages/contracts/openapi/openapi.json
packages/contracts/python/src/study_contracts/__init__.py
packages/contracts/python/src/study_contracts/note_workflow.py
packages/contracts/python/tests/test_note_workflow_contracts.py
services/api/src/study_agent/api/errors.py
services/api/src/study_agent/api/routers/workspace.py
services/api/src/study_agent/api/schemas/__init__.py
services/api/src/study_agent/api/schemas/note_workflow.py
services/api/src/study_agent/config.py
services/api/tests/contract/test_openapi.py
services/api/tests/integration/test_worker_presence.py
services/api/tests/integration/test_workspace_api.py
services/api/tests/unit/test_config.py
services/api/tests/unit/test_errors_and_redaction.py
tests/e2e/mockApi.ts
EOF
sort -u -o "$EXPECTED_PATHS" "$EXPECTED_PATHS"
diff -u "$EXPECTED_PATHS" "$ACTUAL_PATHS"
rm "$EXPECTED_PATHS" "$ACTUAL_PATHS"
```

Expected: `diff` exits 0. Also verify no route leaked through an indirect import:

```bash
uv run alembic -c services/api/alembic.ini heads
! rg -n 'note_batches_router|/note-batches|versions/\{version\}' \
  services/api/src/study_agent/main.py \
  packages/contracts/openapi/openapi.json \
  apps/web/src/api/generated/schema.ts
```

Expected: Alembic is still 0007 and the search has no match.

### Step 8: Stage explicitly and create one independent commit

Do not use `git add .` or `git add -A`. Stage exactly the allowlist from Scope, then inspect:

```bash
git add \
  apps/web/src/api/generated/schema.ts \
  apps/web/src/test/render.tsx \
  packages/contracts/openapi/openapi.json \
  packages/contracts/python/src/study_contracts/__init__.py \
  packages/contracts/python/src/study_contracts/note_workflow.py \
  packages/contracts/python/tests/test_note_workflow_contracts.py \
  services/api/src/study_agent/api/errors.py \
  services/api/src/study_agent/api/routers/workspace.py \
  services/api/src/study_agent/api/schemas/__init__.py \
  services/api/src/study_agent/api/schemas/note_workflow.py \
  services/api/src/study_agent/config.py \
  services/api/tests/contract/test_openapi.py \
  services/api/tests/integration/test_worker_presence.py \
  services/api/tests/integration/test_workspace_api.py \
  services/api/tests/unit/test_config.py \
  services/api/tests/unit/test_errors_and_redaction.py \
  tests/e2e/mockApi.ts
git diff --cached --name-only
git diff --cached --check
git diff --cached --stat
git status --short
```

Expected: exactly 17 staged paths, no unstaged slice changes, and no excluded/untracked path. Commit:

```bash
git commit -m "feat: add note workflow contracts and capability gates"
test "$(git rev-list --count "$BASE_SHA"..HEAD)" = 1
test "$(git merge-base "$BASE_SHA" HEAD)" = "$BASE_SHA"
git status --short --branch
git show --stat --oneline --decorate HEAD
```

Expected: one commit directly above `main@dd239e9`, a clean target worktree, and no upstream push.

## Test plan

- Python contract: request discrimination, uniqueness, status/reason invariants, terminal/time
  invariants, ETA exclusivity, command targets, AST/reference closure, coverage basis, and export
  timestamp validation.
- Error boundary: every sensitive category independently triggers generic public detail; safe
  actionable detail remains; retry metadata and the complete stable error-code set serialize.
- Configuration: defaults remain off; runner/chat/embedding and every cross-field prerequisite
  fail closed independently; all-gates-enabled settings validate without network access.
- API capability: default projection is disabled/unavailable; fully configured projection is
  enabled/available; parse-worker presence does not masquerade as Note Runner liveness.
- Legacy Notes: existing synchronous create/read/edit/regenerate integration tests remain green and
  unchanged.
- OpenAPI/TypeScript: path map is identical to Plan 001 main; exactly one component is added;
  generated files are deterministic and contain no dormant batch/version/export contracts.
- Web/E2E: typed fixtures and the Playwright capability mock match the required response while all
  existing UI behavior remains unchanged.
- Repository: Alembic stays at sole head 0007; full non-live Python, Web Vitest, Playwright,
  typechecks, lint, format, and diff hygiene pass.

## Done criteria

- [ ] Target branch/worktree starts clean from pinned `origin/main@dd239e9`; checkpoint remains
      untouched and is used only as a blob source.
- [ ] Exactly the 17 allowlisted paths differ from base, including untracked-file accounting.
- [ ] `study_contracts.note_workflow` and its P1 facade/tests are present; `study_contracts.notes`
      has no diff and no historical Note contract is imported.
- [ ] All note settings default off and runner/chat/embedding plus cross-field relations fail closed
      independently.
- [ ] Public detail sanitization catches field-prefixed synthetic local paths without suppressing a
      normal safe detail.
- [ ] Capability API tests cover default-off and fully configured mappings without external calls
      or runtime-liveness claims.
- [ ] OpenAPI paths equal base, only `NoteWorkflowCapabilityResponse` is new, conversation contracts
      remain, and legacy Notes remains `201 NoteResponse`.
- [ ] Generated JSON/TypeScript reproduce byte-for-byte and contain no note-batch, historical
      version, generation, AST, coverage, ETA, or export public schema.
- [ ] Alembic reports exactly `20260721_0007 (head)` and no migration/ORM/module/router diff exists.
- [ ] Focused and full Python tests, full Web Vitest, 20 targeted Playwright cases, Web/Python
      typechecks, Web lint, Ruff format/lint, generated check, and diff hygiene all pass.
- [ ] One clean local commit exists on `codex/note-workflow-contract-capability`; nothing was
      pushed, merged, or added to AIWF status.

## STOP conditions

Stop and report the exact command, path, and dependency; do not widen the slice if any is true:

- `origin/main`, checkpoint, or merge-base differs from the three pinned SHAs.
- The target branch or worktree already exists, or the target worktree is not clean at base.
- The repository's standard dependency bootstrap fails or changes `uv.lock`/`package-lock.json`.
- A path classified as whole-file has Plan 001 drift or now imports P2/P3/P4 implementation code.
- The Python contract/facade/tests cannot pass without modifying `study_contracts.notes`, adding a
  migration/ORM/repository/router/runner, or importing AST/version implementation.
- Capability correctness is interpreted to require live Note Runner presence. That belongs to a
  later runner slice; this slice exposes configuration readiness only.
- QAPage, Notes UI, Web API client, or product capability UI must change to satisfy generated types.
- OpenAPI path maps differ from base, any component other than
  `NoteWorkflowCapabilityResponse` is added, or conversation/legacy Notes contracts change.
- Generated TypeScript contains any dormant Python contract or a batch/version/export route.
- Alembic reports multiple heads or any head other than `20260721_0007`.
- A test failure requires checkpoint migration 7102/0008, note ORM/version/AST/backfill/lifecycle,
  batch state/service/repository/runner, DOCX/export/cleanup, exact ETA, or external provider work.
- A focused correction would require a path outside the exact 17-path allowlist.
- The final tracked-plus-untracked path comparison or cached diff contains any extra path.

## Maintenance notes

- Dormant Python contracts are not public HTTP promises until a later route registers them. Later
  slices must deliberately decide when each schema enters OpenAPI and regenerate from that app.
- `note_workflow_configured` is configuration readiness, not process liveness. Do not reuse its
  label as production health evidence.
- The sanitizer is global API behavior. Keep independent positive and negative tests whenever its
  marker grammar changes.
- Future note-version work must add historical response models in its own slice; do not amend this
  commit to pre-land `study_contracts.notes` changes.
- OpenAPI JSON and generated TypeScript are generated artifacts. Review their structural delta,
  not file size, and never patch them manually.
