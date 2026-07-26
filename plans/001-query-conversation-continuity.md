# Plan 001: Isolate and deliver persistent query conversations

> **Executor instructions**: Read this plan completely before changing code. Follow the
> steps in order and run every verification command. Do not copy a whole file from the
> checkpoint when it appears in the "mixed files" table. If any STOP condition occurs,
> stop and report the exact command, file, and dependency; do not pull note-workflow code
> into this slice to make a check pass.
>
> **Planning-time blocker (resolved before execution)**: On 2026-07-22, neither a local nor an
> `origin` ref named
> `codex/workflow-checkpoint` existed. The intended source code existed only as uncommitted
> changes in the `main` worktree. An operator must first materialize that exact mixed state
> as an immutable `codex/workflow-checkpoint` commit. Creating or guessing that source
> commit is outside this plan. The operator subsequently materialized the source as
> `codex/workflow-checkpoint@7c08333`, so this condition no longer blocks execution.
>
> **Drift check (run first)**:
>
> ```bash
> git rev-parse --verify 856cb1d5b39241e6591b0396a161764649dc0832^{commit}
> git rev-parse --verify refs/heads/codex/workflow-checkpoint^{commit}
> git diff --stat 856cb1d5b39241e6591b0396a161764649dc0832..origin/main -- \
>   services/api/alembic/versions \
>   services/api/src/study_agent/infrastructure/db/models/answers.py \
>   services/api/src/study_agent/modules/answering \
>   services/api/src/study_agent/api/routers/queries.py \
>   services/api/src/study_agent/providers \
>   apps/web/src/api \
>   apps/web/src/features/qa \
>   tests/e2e
> ```
>
> Expected: the first two commands resolve commits and the final command prints no in-scope
> drift. If the checkpoint ref is missing, or `origin/main` changed any in-scope path, STOP.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MED
- **Depends on**: none
- **Category**: migration / correctness / tests
- **Planned at**: commit `856cb1d`, 2026-07-22
- **Target branch**: `codex/query-conversation-continuity`
- **Target commit shape**: one independently verifiable commit; do not push or open a PR
- **Reconcile verdict**: DONE at target commit `9682969`, 2026-07-22
- **Prior reconcile verdict**: REVISE at target commit `7f41817`, 2026-07-22

## Reconcile review 2 - DONE

The executor amended the feature commit from `7f41817` to `9682969`. The revision changes only
`services/api/tests/integration/test_queries.py`, adding a PostgreSQL-backed boundary test; no
production or out-of-scope path changed. The target worktree remains clean, the branch still has
one commit based directly on `origin/main@856cb1d`, and it has no configured upstream.

`test_recent_context_enforces_default_turn_and_character_budgets` now constructs six eligible
history turns plus the current query and verifies all requested behavior: the newest four turns
are returned chronologically, older turns and the current query are absent, questions and answers
truncate at 1,000 and 1,500 characters, total default context is exactly 6,000 characters in the
boundary fixture, and the newest context survives budget exhaustion.

Second-round verification results:

- `test_queries.py`: 5 passed.
- Full non-live Python suite: 425 passed, 1 deselected.
- Ruff format and lint for the revised test: passed.
- Alembic head: exactly `20260721_0007 (head)`.
- Revision scope: 209 inserted lines in `test_queries.py` only; diff hygiene passed.
- Previously verified Web Vitest 40/40, QA Playwright 4/4, OpenAPI/TypeScript exact generation,
  typechecks, migration tests, and excluded-scope audit remain valid because the amend changed
  only the backend integration test.

One possible mutation-test strengthening was considered and rejected as a completion blocker:
the synthetic current query has an ineligible `pending` status. In production,
`QueryService.execute()` calls `recent_context()` after `start_retrieval()` has set that query to
the likewise ineligible `retrieving` state, and the SQL's strict `(created_at, id)` predecessor
predicate independently excludes the current row. The existing assertion therefore matches the
real call path; testing an eligible synthetic current state may be added later but is not required
for this slice.

Plan 001 is DONE. Merging remains an operator decision; this review did not merge or push.

## Reconcile review 1 - REVISE

The target worktree at
`/Users/melody/Desktop/all_for_ending_week-query-conversation-continuity` is clean and contains
one commit based directly on `origin/main@856cb1d`. All 31 changed paths are allowlisted by this
plan. No excluded note-workflow migration, Python contract, Notes UI, capability, `.idea`, or
AIWF path is present. Migration 0007 is the sole Alembic delta and sole head; its PostgreSQL
upgrade, legacy backfill, downgrade, and re-upgrade checks passed independently of 0008.

The implementation is not approved yet because the required bounded-history test is incomplete.
`services/api/tests/integration/test_queries.py::test_follow_up_uses_bounded_non_evidence_context_and_new_conversation_resets_it`
uses only two short prior turns. It does not fail if the four-turn SQL limit, 6,000-character
aggregate budget, 1,000-character question truncation, or 1,500-character answer truncation is
removed. This is a test-quality failure against this plan's explicit bounded-history test target,
not a known production-code defect.

Required revision, limited to `services/api/tests/integration/test_queries.py` unless the test
reveals a real defect:

1. Add PostgreSQL-backed coverage with more than four eligible prior turns in one conversation;
   assert the default result contains the newest four turns in chronological order and excludes
   both the older turns and current query.
2. Use long, source-current answered turns to cross the default 6,000-character aggregate budget;
   assert the returned question/answer text never exceeds 1,000/1,500 characters respectively,
   total returned context never exceeds 6,000 characters, and the newest context is retained.
3. Keep the evidence trust-boundary assertions already present. Do not change production code
   merely to satisfy a weaker assertion. If the new behavioral test exposes a mismatch, STOP and
   report it for a fresh review.
4. Amend the existing feature commit after the focused and full gates pass so the branch retains
   the plan's one-commit shape. Do not push.

Re-run after revision:

```bash
TEST_DATABASE_URL=postgresql+asyncpg://study_agent@127.0.0.1:54329/study_agent_test_codex_session_20260722 \
  uv run pytest services/api/tests/integration/test_queries.py -q
TEST_DATABASE_URL=postgresql+asyncpg://study_agent@127.0.0.1:54329/study_agent_test_codex_session_20260722 \
  uv run pytest -m "not live" -q
uv run ruff format --check services/api/tests/integration/test_queries.py
uv run ruff check services/api/tests/integration/test_queries.py
git diff --check origin/main...HEAD
git status --short --branch
```

Expected: all tests and Ruff checks pass, diff check has no output, and the target worktree is
clean with one unpushed commit. The already verified gates at `7f41817` were: 3 migration tests,
16 focused API/database tests, 424 non-live Python tests (1 deselected), 40 Web Vitest tests,
4 QA Playwright projects, Web/Python typechecks, Ruff format/lint, exact OpenAPI and generated
TypeScript reproduction, and diff hygiene.

## Why this matters

The mixed checkpoint contains a complete query-conversation implementation and several
unfinished note-workflow phases. The query slice must land independently so existing queries
are migrated into persistent, principal-scoped conversations; follow-up context remains
bounded and non-evidentiary; and the QA page can restore, switch, and continue threads. A
clean extraction also restores the migration and Playwright gates without importing note
contracts, schemas, runners, export, cleanup, or ETA work.

## Current state

- `origin/main` and the current `main` HEAD were both
  `856cb1d5b39241e6591b0396a161764649dc0832` when this plan was written.
- The intended checkpoint was a dirty `main` worktree, not a branch. It contained 45 modified
  files and many untracked files, including both conversation and note-workflow work.
- `services/api/alembic/versions/20260721_0007_query_conversations.py:17-20` declares
  `revision = "20260721_0007"`, `down_revision = "20260719_0006"`, and no `depends_on`.
  Static review found no dependency on `7102eb21ee91` or `20260722_0008`.
- Migration 0007 creates `conversations`, backfills one deterministic `历史问答` conversation
  per `(user_id, course_id)`, makes `query_runs.conversation_id` non-null, adds the scoped FK,
  and provides a downgrade that preserves pre-existing query rows.
- `ConversationModel` and the `QueryRunModel.conversation_id` mapping are in
  `services/api/src/study_agent/infrastructure/db/models/answers.py`. The public model export
  file is mixed with note-version and note-workflow imports, so only the conversation export
  may be extracted there.
- `QueryRepository.create()` locks the owned course before resolving or creating the first
  conversation. Preserve that lock: it serializes concurrent first-query creation.
- Conversation history is deliberately bounded by 4 turns and 6,000 characters. Retrieval
  receives historical questions only; the chat provider receives bounded question/answer
  context marked as untrusted non-evidence. Historical answers must never become citations or
  bypass the current evidence gate.
- Query API schemas live in `services/api/src/study_agent/api/routers/queries.py`, not in the
  Python `study_contracts` package. Therefore this slice must not change
  `packages/contracts/python/**`.
- The current mixed OpenAPI and generated TypeScript include note-batch schemas. Do not copy
  either generated file from the checkpoint. Regenerate them after only the conversation API
  is present on the clean branch.
- `QAPage.tsx` imports only query/conversation API types and shared workspace UI; it has no note
  UI dependency. Its checkpoint implementation can be extracted without the unfinished Notes
  page. On a clean generated schema, `apps/web/src/test/render.tsx` must remain unchanged.
- The existing Playwright mock has only `/courses/{course_id}/queries`; it lacks conversation
  list/create/history routes and omits `conversation_id` from query snapshots. Consequently the
  submit button remains disabled after the initial conversation request returns 404.
- Three baseline integration fixtures directly construct `QueryRunModel` and must be adjusted
  for the new non-null FK: `test_full_deletion.py`, `test_sources_api.py`, and two tests in
  `test_workspace_api.py`. Their checkpoint files also contain or sit beside unrelated note
  work, so extract only the conversation seed hunks.
- Repository style uses Ruff with 100-character lines, strict mypy, local Pydantic request/
  response models in FastAPI routers, TanStack Query cache keys in the Web app, Vitest for
  components, and stateful route mocks in `tests/e2e/mockApi.ts`.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Alembic head | `uv run alembic -c services/api/alembic.ini heads` | exactly `20260721_0007 (head)` |
| Migration tests | `uv run pytest services/api/tests/integration/test_answering_migration.py -q` | all collected tests pass |
| Python/API tests | `uv run pytest -m "not live" -q` | all collected tests pass |
| Web focused tests | `npm test --workspace @study-agent/web -- src/api/client.test.ts src/features/qa/QAPage.test.tsx` | all collected tests pass |
| Web full tests | `npm test --workspace @study-agent/web` | all tests pass |
| QA Playwright | `npm run test:e2e -- --grep "answered and abstained queries remain visibly distinct"` | 4 passed: Chromium/WebKit desktop/mobile |
| Generate API | `npm run generate:api` | exits 0; only conversation contract deltas are generated |
| Generated TS check | `npm exec --workspace @study-agent/web openapi-typescript -- ../../packages/contracts/openapi/openapi.json --check` | exits 0 |
| Web typecheck | `npm run typecheck --workspace @study-agent/web` | exits 0, no errors |
| Python typecheck | `MYPYPATH=packages/contracts/python/src:services/api/src:services/worker/src uv run mypy -p study_contracts -p study_agent -p study_worker` | `Success: no issues found` |
| Python format | `uv run ruff format --check .` | exits 0 |
| Python lint | `uv run ruff check .` | exits 0 |
| Diff hygiene | `git diff --check` | no output, exit 0 |

All PostgreSQL tests require `TEST_DATABASE_URL` to point to an explicitly disposable database
whose name contains `test`. Never run migration/downgrade tests against a development or user
database.

## Scope

### Whole-file extraction allowed

These checkpoint diffs were reviewed as conversation-only. Restore them from `SOURCE_SHA` after
the Step 0 ancestry/drift checks:

- `services/api/alembic/versions/20260721_0007_query_conversations.py` — entire migration.
- `services/api/src/study_agent/infrastructure/db/models/answers.py` — `ConversationModel` and
  the `QueryRunModel` conversation FK/index/column.
- `services/api/src/study_agent/modules/answering/queries.py` — `ConversationSnapshot`, bounded
  context helpers, conversation CRUD/listing, query association, and `QueryService.execute()`.
- `services/api/src/study_agent/modules/answering/prompts.py` — propagate bounded context into
  `EvidencePrompt` without changing evidence passages.
- `services/api/src/study_agent/modules/answering/service.py` — pass conversation context through
  the existing evidence-gated answer path.
- `services/api/src/study_agent/api/routers/queries.py` — `ConversationCreate`,
  `ConversationResponse`, conversation endpoints, and `conversation_id` request/response fields.
- `services/api/src/study_agent/providers/deepseek.py` — non-evidence trust-boundary system text
  and serialized `conversation_context`.
- `services/api/tests/integration/test_answering_migration.py` — 0007 head, backfill, downgrade,
  and re-upgrade tests.
- `services/api/tests/integration/test_queries.py` — conversation scoping/history and bounded
  follow-up context integration tests.
- `services/api/tests/unit/answering/test_service.py` — conversation/evidence separation test.
- `services/api/tests/unit/answering/test_conversation_context.py` — retrieval-query context test.
- `services/api/tests/contract/test_deepseek_provider.py` — provider payload trust-boundary test.
- `apps/web/src/api/client.ts` — conversation methods and optional `conversation_id` in
  `createQuery()`.
- `apps/web/src/api/client.test.ts` — atomic first query and existing-thread request assertions.
- `apps/web/src/api/types.ts` — `ConversationRecord` and `ConversationCreate` generated aliases.
- `apps/web/src/features/qa/QAPage.tsx` — conversation list, switching, history, first-query
  atomic creation, multi-turn rendering, and cache updates.
- `apps/web/src/features/qa/QAPage.test.tsx` — conversation restoration/switch/create/failure and
  invalidated-cache tests.
- `apps/web/src/features/qa/queryPolling.ts` — treat `invalidated` as terminal for restored turns.
- `apps/web/src/test/fixtures.ts` — add `conversation_id` to query snapshots.
- `apps/web/src/styles/workspace.css` — `.qa-conversations*` and `.qa-turn*` rules only.
- `apps/web/src/styles/responsive.css` — `.qa-conversations*` responsive rules only.

Before restoring any whole file, inspect its source diff. If it contains `note_workflow`,
`NoteBatch`, `NoteVersion`, `NoteExport`, `StoragePurpose`, `note_batches`, or imports from
`study_agent.modules.notes`, do not restore it wholesale; STOP and report source drift.

### Mixed files: apply only the listed symbols/hunks

| File | Allowed hunk/symbol | Explicitly reject |
|---|---|---|
| `services/api/src/study_agent/infrastructure/db/models/__init__.py` | Add `ConversationModel` to the `answers` import and `__all__` | All `note_versions`, `note_workflow`, export, cleanup, batch imports/exports |
| `services/api/src/study_agent/providers/protocols.py` | Add `ConversationContextTurn`; add `EvidencePrompt.conversation_context` | `Literal` import, `StoragePurpose`, and changing `ObjectScope.purpose` |
| `services/api/src/study_agent/providers/__init__.py` | Import/export `ConversationContextTurn` | `StoragePurpose` |
| `services/api/tests/integration/test_full_deletion.py` | Import `ConversationModel`; create/flush one scoped conversation immediately before the existing query; pass `conversation_id` | datetime/export/version/lifecycle/redaction additions |
| `services/api/tests/integration/test_sources_api.py` | Import `ConversationModel`; seed/flush one conversation in `_seed_slide_citation`; pass `conversation_id` | Any unrelated source behavior change |
| `services/api/tests/integration/test_workspace_api.py` | Import `ConversationModel`; seed/flush conversations in the two tests that directly construct `QueryRunModel`; pass `conversation_id` | Capability/note workflow changes or unrelated Lab changes |

Never use `git restore`, `git checkout`, or whole-file copy for a mixed file. Start with the
`origin/main` version and apply only the allowlisted edits using a patch editor. Then inspect
`git diff -- <file>` before proceeding.

### Generate or author on the clean branch

- `services/api/tests/contract/test_openapi.py` — keep baseline tests and add only a conversation
  contract assertion; do not take the checkpoint's note-workflow assertion.
- `packages/contracts/openapi/openapi.json` — regenerate from the clean FastAPI app.
- `apps/web/src/api/generated/schema.ts` — regenerate from the clean OpenAPI JSON.
- `tests/e2e/mockApi.ts` — add stateful conversation routes and `conversation_id` fields; this
  file was not changed in the checkpoint.

### Explicitly out of scope

- `packages/contracts/python/**`, including `note_workflow.py` and all note-version contracts.
- `services/api/alembic/versions/7102eb21ee91_note_workflow_expand.py` and
  `20260722_0008_note_batch_commands.py`.
- `services/api/src/study_agent/config.py`, `main.py`, `api/routers/workspace.py`,
  `api/routers/note_batches.py`, `api/routers/notes.py`, and `api/schemas/**`.
- `services/api/src/study_agent/infrastructure/db/models/core.py`, `note_versions.py`, and
  `note_workflow.py`.
- All `services/api/src/study_agent/modules/notes/**`, note runner, export, cleanup, DOCX, and ETA.
- `apps/web/src/features/notes/**`, `apps/web/src/test/render.tsx`, note-batch client/UI, and any
  capability UI.
- `tests/e2e/qa-notes.spec.ts` is verification-only; do not change its product expectations just
  to make the mock pass.
- `.idea/**`, planning/AIWF documents, new product behavior, and unrelated refactors.

Legacy synchronous notes and the notes tables already present in migration 0006 are baseline;
their presence is not permission to add the new note workflow.

## Git workflow

- Source ref: immutable local `codex/workflow-checkpoint` commit, created by the operator before
  this plan starts.
- Base: the verified `origin/main` commit, expected to be `856cb1d` unless the drift check proves
  no in-scope changes.
- Target: create `codex/query-conversation-continuity` in a separate sibling worktree so the
  mixed checkpoint worktree remains untouched.
- Commit only after every gate passes. Use one commit:
  `feat: add persistent query conversations`.
- Do not push, merge, rebase the checkpoint, or open a PR.
- The advisor index lives in the original worktree and is not part of the feature commit; the
  operator/reviewer updates it after execution.

## Steps

### Step 0: Validate the immutable source and create a clean target worktree

From the original repository worktree:

```bash
git fetch --prune origin
BASE_SHA=$(git rev-parse origin/main)
SOURCE_SHA=$(git rev-parse --verify refs/heads/codex/workflow-checkpoint^{commit})
test "$(git merge-base "$BASE_SHA" "$SOURCE_SHA")" = "$BASE_SHA"
git show "$SOURCE_SHA:services/api/alembic/versions/20260721_0007_query_conversations.py" \
  | rg 'revision: str = "20260721_0007"|down_revision: str \| None = "20260719_0006"'
git show "$SOURCE_SHA:apps/web/src/features/qa/QAPage.tsx" \
  | rg 'listConversations|listConversationQueries|createConversation'
git branch --list codex/query-conversation-continuity
```

Expected: ancestry check exits 0; both source-file probes match; the final branch command prints
nothing. If the source ref does not exist, is not based on `origin/main`, lacks either source
file, or the target branch already exists, STOP.

Create a sibling worktree with an explicit path, then enter it:

```bash
TARGET_WORKTREE=/Users/melody/Desktop/all_for_ending_week-query-conversation-continuity
test ! -e "$TARGET_WORKTREE"
git worktree add -b codex/query-conversation-continuity "$TARGET_WORKTREE" "$BASE_SHA"
git -C "$TARGET_WORKTREE" status --short --branch
git -C "$TARGET_WORKTREE" rev-parse HEAD
```

Expected: status is `## codex/query-conversation-continuity` with no file entries, and HEAD is
`BASE_SHA`. Re-export `BASE_SHA` and `SOURCE_SHA` in every new shell session.

### Step 1: Confirm the source hunk classification before extraction

In the target worktree, inspect the source diff without applying it:

```bash
git diff --name-status "$BASE_SHA" "$SOURCE_SHA" -- \
  services/api/alembic/versions \
  services/api/src/study_agent/infrastructure/db/models \
  services/api/src/study_agent/modules/answering \
  services/api/src/study_agent/api/routers/queries.py \
  services/api/src/study_agent/providers \
  services/api/tests \
  apps/web/src/api \
  apps/web/src/features/qa \
  apps/web/src/styles \
  tests/e2e
git diff --unified=3 "$BASE_SHA" "$SOURCE_SHA" -- \
  services/api/src/study_agent/infrastructure/db/models/__init__.py \
  services/api/src/study_agent/providers/protocols.py \
  services/api/src/study_agent/providers/__init__.py \
  services/api/tests/integration/test_full_deletion.py \
  services/api/tests/integration/test_sources_api.py \
  services/api/tests/integration/test_workspace_api.py
```

Expected: the mixed-file hunks match the allowlist above. If the checkpoint has additional
changes inside a file classified as whole-file, reclassify only when the extra hunk is clearly
conversation-only; otherwise STOP and report the drift.

### Step 2: Extract migration 0007 and the matching ORM only

Restore these conversation-only files from the source commit:

```bash
git restore --source "$SOURCE_SHA" -- \
  services/api/alembic/versions/20260721_0007_query_conversations.py \
  services/api/src/study_agent/infrastructure/db/models/answers.py
```

Manually patch `services/api/src/study_agent/infrastructure/db/models/__init__.py` with only the
`ConversationModel` import and `__all__` entry. Confirm migration/ORM agreement:

- `conversations` has `(id, course_id, user_id)` scoped uniqueness.
- `query_runs.conversation_id` is non-null.
- Both migration and ORM use `fk_query_runs_conversation_scope` over
  `(conversation_id, course_id, user_id)` with `ON DELETE CASCADE`.
- Both define `ix_query_runs_conversation_created` and
  `ix_conversations_scope_updated` with the same columns.

Verify:

```bash
{
  git diff --name-only "$BASE_SHA" -- services/api/alembic/versions
  git ls-files --others --exclude-standard -- services/api/alembic/versions
} | sort -u
uv run alembic -c services/api/alembic.ini heads
rg -n '7102eb21ee91|20260722_0008|note_workflow|note_generation|note_exports' \
  services/api/alembic/versions/20260721_0007_query_conversations.py \
  services/api/src/study_agent/infrastructure/db/models/answers.py \
  services/api/src/study_agent/infrastructure/db/models/__init__.py
```

Expected: the migration diff lists only 0007; Alembic reports exactly
`20260721_0007 (head)`; the forbidden-term search prints nothing. Any additional Alembic head or
need to import note ORM metadata is a STOP condition.

### Step 3: Extract repository, service, router, and provider context behavior

Restore the conversation-only backend files:

```bash
git restore --source "$SOURCE_SHA" -- \
  services/api/src/study_agent/modules/answering/queries.py \
  services/api/src/study_agent/modules/answering/prompts.py \
  services/api/src/study_agent/modules/answering/service.py \
  services/api/src/study_agent/api/routers/queries.py \
  services/api/src/study_agent/providers/deepseek.py
```

Apply only the allowlisted `ConversationContextTurn` hunks to
`providers/protocols.py` and `providers/__init__.py`. Preserve all baseline storage types.

Review these load-bearing behaviors against the checkpoint source:

1. `QueryRepository.create()` accepts optional `conversation_id`, locks the course, resolves an
   existing scoped conversation or creates the first one atomically, assigns the query, and
   updates the automatic title.
2. `create_conversation()`, `list_conversations()`, and `list_for_conversation()` enforce the
   principal/course scope and deterministic ordering/limits.
3. `recent_context()` excludes the current query, caps turn/character counts, preserves
   unavailable historical answers as `None`, and never treats prior answers as evidence.
4. `_contextual_retrieval_query()` includes historical questions but not answer text.
5. `QueryService.execute()` passes the same bounded context to retrieval and the trusted answer
   service without bypassing the current evidence gate.
6. `DeepSeekChatProvider._request_payload()` labels context
   `untrusted_non_evidence_conversation_context`; the system prompt forbids citing it.
7. Router endpoints are exactly:
   `POST/GET /api/v1/courses/{course_id}/conversations` and
   `GET /api/v1/conversations/{conversation_id}/queries`; existing query creation remains
   backward-compatible when `conversation_id` is omitted.

Verify scope and lint early:

```bash
rg -n 'note_workflow|NoteBatch|NoteVersion|NoteExport|StoragePurpose|modules\.notes' \
  services/api/src/study_agent/modules/answering \
  services/api/src/study_agent/api/routers/queries.py \
  services/api/src/study_agent/providers/protocols.py \
  services/api/src/study_agent/providers/__init__.py \
  services/api/src/study_agent/providers/deepseek.py
uv run ruff check \
  services/api/src/study_agent/infrastructure/db/models/answers.py \
  services/api/src/study_agent/infrastructure/db/models/__init__.py \
  services/api/src/study_agent/modules/answering/queries.py \
  services/api/src/study_agent/modules/answering/prompts.py \
  services/api/src/study_agent/modules/answering/service.py \
  services/api/src/study_agent/api/routers/queries.py \
  services/api/src/study_agent/providers/protocols.py \
  services/api/src/study_agent/providers/__init__.py \
  services/api/src/study_agent/providers/deepseek.py
```

Expected: forbidden-term search prints nothing and Ruff exits 0.

### Step 4: Extract and repair Python/API tests, then prove 0007 is independent

Restore the conversation-only test files:

```bash
git restore --source "$SOURCE_SHA" -- \
  services/api/tests/integration/test_answering_migration.py \
  services/api/tests/integration/test_queries.py \
  services/api/tests/unit/answering/test_service.py \
  services/api/tests/unit/answering/test_conversation_context.py \
  services/api/tests/contract/test_deepseek_provider.py
```

Patch only the allowlisted conversation fixture hunks in:

- `test_full_deletion.py`: one scoped `ConversationModel` before the existing `query-1`.
- `test_sources_api.py`: one scoped conversation in `_seed_slide_citation`.
- `test_workspace_api.py`: one scoped conversation in each of the two existing Lab tests that
  construct a `QueryRunModel`.

Do not copy the checkpoint's note lifecycle/export additions from any of these files. Search all
remaining direct query constructors and ensure every baseline constructor has a valid
conversation:

```bash
rg -n -C 8 'QueryRunModel\(' services/api --glob '*.py'
```

Expected: application construction occurs in `QueryRepository.create()`; every direct baseline
test construction supplies `conversation_id`. A constructor newly added only for excluded note
tests must not exist in the target branch.

Format only the Python files in this slice:

```bash
uv run ruff format \
  services/api/alembic/versions/20260721_0007_query_conversations.py \
  services/api/src/study_agent/infrastructure/db/models/answers.py \
  services/api/src/study_agent/infrastructure/db/models/__init__.py \
  services/api/src/study_agent/modules/answering/queries.py \
  services/api/src/study_agent/modules/answering/prompts.py \
  services/api/src/study_agent/modules/answering/service.py \
  services/api/src/study_agent/api/routers/queries.py \
  services/api/src/study_agent/providers/protocols.py \
  services/api/src/study_agent/providers/__init__.py \
  services/api/src/study_agent/providers/deepseek.py \
  services/api/tests/integration/test_answering_migration.py \
  services/api/tests/integration/test_queries.py \
  services/api/tests/integration/test_full_deletion.py \
  services/api/tests/integration/test_sources_api.py \
  services/api/tests/integration/test_workspace_api.py \
  services/api/tests/unit/answering/test_service.py \
  services/api/tests/unit/answering/test_conversation_context.py \
  services/api/tests/contract/test_deepseek_provider.py
```

Run unit/contract tests first:

```bash
uv run pytest \
  services/api/tests/unit/answering/test_conversation_context.py \
  services/api/tests/unit/answering/test_service.py \
  services/api/tests/contract/test_deepseek_provider.py -q
```

Expected: all collected tests pass.

Then use an explicitly disposable PostgreSQL test database:

```bash
: "${TEST_DATABASE_URL:?Set TEST_DATABASE_URL to a disposable PostgreSQL test database}"
case "$TEST_DATABASE_URL" in *test*) ;; *) echo 'Refusing non-test database' >&2; exit 2;; esac
uv run pytest services/api/tests/integration/test_answering_migration.py -q
uv run pytest \
  services/api/tests/integration/test_queries.py \
  services/api/tests/integration/test_full_deletion.py \
  services/api/tests/integration/test_sources_api.py \
  services/api/tests/integration/test_workspace_api.py -q
```

Expected: all tests pass. The migration test must observe head `20260721_0007`, migrate a database
at 0006 without any new note-workflow tables, group existing queries per course, downgrade back
to 0006 without losing queries, and re-upgrade successfully. If this requires 7102/0008 or note
ORM/schema, STOP and report the exact missing dependency.

### Step 5: Generate only the conversation OpenAPI and TypeScript contract

Start from the `origin/main` version of
`services/api/tests/contract/test_openapi.py`. Add one test named, for example,
`test_query_conversation_contract_is_present_without_note_workflow_routes` that asserts:

- The three conversation operations exist.
- `ConversationCreate` and `ConversationResponse` exist.
- `QueryCreate` exposes optional `conversation_id`.
- `QueryResponse` requires `conversation_id`.
- No path contains `note-batches` and no schema name starts with `NoteBatch`, `NoteExport`,
  `NoteGeneration`, or `NoteVersion`.

Do not copy the checkpoint's `test_note_workflow_capability_contract...` function.

Generate from the clean app:

```bash
npm run generate:api
uv run ruff format services/api/tests/contract/test_openapi.py
uv run pytest services/api/tests/contract/test_openapi.py -q
npm exec --workspace @study-agent/web openapi-typescript -- \
  ../../packages/contracts/openapi/openapi.json --check
```

Expected: generation and tests pass; the generated check exits 0.

Validate JSON structurally rather than editing generated text by hand:

```bash
uv run python - <<'PY'
import json
from pathlib import Path

document = json.loads(Path("packages/contracts/openapi/openapi.json").read_text())
paths = document["paths"]
schemas = document["components"]["schemas"]
assert "/api/v1/courses/{course_id}/conversations" in paths
assert "/api/v1/conversations/{conversation_id}/queries" in paths
assert all("note-batches" not in path for path in paths)
assert {"ConversationCreate", "ConversationResponse"} <= schemas.keys()
for prefix in ("NoteBatch", "NoteExport", "NoteGeneration", "NoteVersion"):
    assert not any(name.startswith(prefix) for name in schemas)
PY
git status --short -- packages/contracts/python
```

Expected: the script exits 0 and the Python-contract status command prints nothing. If clean
generation emits new note-workflow schemas, STOP and locate the backend contamination; never
delete generated chunks manually.

### Step 6: Extract the Web API client and conversation UI

Restore the reviewed conversation-only Web files:

```bash
git restore --source "$SOURCE_SHA" -- \
  apps/web/src/api/client.ts \
  apps/web/src/api/client.test.ts \
  apps/web/src/api/types.ts \
  apps/web/src/features/qa/QAPage.tsx \
  apps/web/src/features/qa/QAPage.test.tsx \
  apps/web/src/features/qa/queryPolling.ts \
  apps/web/src/test/fixtures.ts \
  apps/web/src/styles/workspace.css \
  apps/web/src/styles/responsive.css
```

Preserve these behaviors:

- Initial conversation state must load successfully before submission is enabled.
- With no conversation, the first query omits `conversation_id`; the API atomically creates it,
  and the returned `conversation_id` becomes the selected cached thread.
- An explicit new conversation uses the create endpoint and subsequent queries include its ID.
- Restored queries are chronological; switching conversations does not leak prior turns.
- Server `invalidated` state replaces any older answered cache and remains terminal.
- Citation requests remain query-scoped and a source viewer is cleared across course changes.

Verify that no note UI was imported and the baseline capability fixture stayed untouched:

```bash
rg -n 'features/notes|note_workflow|NoteBatch|NoteVersion|NoteExport' \
  apps/web/src/features/qa apps/web/src/api/client.ts apps/web/src/api/types.ts
git status --short -- apps/web/src/test/render.tsx apps/web/src/features/notes
npm test --workspace @study-agent/web -- \
  src/api/client.test.ts src/features/qa/QAPage.test.tsx
npm run typecheck --workspace @study-agent/web
```

Expected: forbidden-term search and excluded-path status print nothing; focused Vitest passes;
Web typecheck exits 0. If typecheck requires adding `note_workflow` to `RuntimeCapabilities` or
changing the Notes page, STOP: the generated contract is contaminated or QAPage has drifted.

### Step 7: Add a stateful Playwright conversation mock

Modify only `tests/e2e/mockApi.ts`; leave `qa-notes.spec.ts` unchanged.

Implement the following mock state and routes using its existing stateful route-handler pattern:

1. Add `conversation_id` to `answered()` and `abstained()` snapshots.
2. Keep an in-memory conversation list and per-conversation query history inside
   `installMockApi()` so each Playwright page starts isolated.
3. `GET /courses/${courseId}/conversations` returns the current list. Initially it may be empty,
   matching the atomic-first-query flow.
4. `POST /courses/${courseId}/conversations` creates a `ConversationResponse` with zero turns,
   selects stable timestamps/IDs, stores it, and returns 201.
5. `GET /conversations/{conversation_id}/queries` returns that thread in chronological order and
   honors the route regardless of the `?limit=100` query string (the mock's `path` excludes the
   query string).
6. `POST /courses/${courseId}/queries` accepts optional `conversation_id`. If absent, create one
   conversation atomically using the question as its title. If present, require an existing
   conversation. Store the answered/abstained snapshot, increment `turn_count`, and update
   `latest_query_id`, `latest_question`, and `updated_at`.
7. Existing query/citation mocks must still return the stored query and citation response.
8. Unknown conversation IDs return the existing structured 404 problem, not a successful empty
   thread.

Run the exact regression gate:

```bash
npm run test:e2e -- --grep "answered and abstained queries remain visibly distinct"
```

Expected: `4 passed`, one in each project declared at
`tests/e2e/playwright.config.ts:29-33`: Chromium desktop/mobile and WebKit desktop/mobile. The
test must submit both questions in the same conversation and retain the answered/abstained visual
distinction. Do not weaken button, answer, citation, or refusal assertions.

### Step 8: Run the complete slice gates and audit the diff boundary

Run gates in this order so failures identify the owning layer:

```bash
uv run alembic -c services/api/alembic.ini heads
uv run pytest -m "not live" -q
npm test --workspace @study-agent/web
npm run test:e2e -- --grep "answered and abstained queries remain visibly distinct"
npm run typecheck --workspace @study-agent/web
MYPYPATH=packages/contracts/python/src:services/api/src:services/worker/src \
  uv run mypy -p study_contracts -p study_agent -p study_worker
uv run ruff format --check .
uv run ruff check .
npm exec --workspace @study-agent/web openapi-typescript -- \
  ../../packages/contracts/openapi/openapi.json --check
git diff --check
```

Expected: Alembic reports only 0007; all tests pass; QA E2E reports 4 passed; all static/format/
generated checks exit 0; `git diff --check` prints nothing.

Audit exclusions:

```bash
{
  git diff --name-only "$BASE_SHA" -- services/api/alembic/versions
  git ls-files --others --exclude-standard -- services/api/alembic/versions
} | sort -u
git status --short -- \
  packages/contracts/python \
  services/api/src/study_agent/config.py \
  services/api/src/study_agent/main.py \
  services/api/src/study_agent/api/routers/workspace.py \
  services/api/src/study_agent/api/routers/notes.py \
  services/api/src/study_agent/infrastructure/db/models/core.py \
  services/api/src/study_agent/modules/notes \
  apps/web/src/features/notes \
  apps/web/src/test/render.tsx \
  tests/e2e/qa-notes.spec.ts \
  .idea
git diff "$BASE_SHA" -- | rg \
  'note_async_workflow|note_runner|note-batches|NoteBatch|NoteExport|NoteGeneration|StorageCleanup|exact ETA|DOCX'
```

Expected: only 0007 appears under Alembic; excluded-path status and forbidden-term search print
nothing. Review `git status --short` and confirm every path belongs to the Scope allowlist.

### Step 9: Stage and create the independent slice commit

Stage only the in-scope files listed in this plan. Do not use `git add .` or `git add -A`.
Before committing:

```bash
git diff --cached --name-only
git diff --cached --check
git diff --cached --stat
git status --short
```

Expected: no `.idea`, `plans`, AIWF, note-workflow, note-version, batch, runner, export, cleanup, or
ETA path is staged; cached diff check prints nothing. Then commit:

```bash
git commit -m "feat: add persistent query conversations"
git status --short --branch
git show --stat --oneline --decorate HEAD
```

Expected: commit succeeds and the target worktree is clean on
`codex/query-conversation-continuity`. Do not push.

## Test plan

- Migration: head is exactly 0007; schema/constraint names match ORM; existing queries backfill
  into one legacy conversation per principal/course; downgrade preserves query rows; re-upgrade
  is deterministic.
- Repository/API: implicit first conversation, explicit conversation creation, auto-title,
  course/principal isolation, 404 for inaccessible/missing conversations, list bounds/order,
  query association, and compatibility course history.
- Context safety: bounded history, historical questions used for retrieval resolution, historical
  answers excluded from retrieval evidence, unavailable dependencies represented safely, new
  conversation resets context, current evidence still required.
- Provider: serialized context is explicitly untrusted non-evidence and cannot be cited.
- OpenAPI/TS: only conversation paths/schemas and `conversation_id` deltas; no new note-workflow
  schema; committed JSON exactly matches generated FastAPI document.
- Web Vitest: restore/switch/create conversations, atomic first query, failed list gate,
  invalidated cache, answered/abstained/provider failure, query-scoped citations.
- Playwright: existing QA scenario passes unchanged in all four browser/viewport projects with
  two questions persisted in one mocked conversation.

## Done criteria

- [ ] Source and base are immutable commits; target branch starts clean from verified
      `origin/main`.
- [ ] The union of tracked and untracked changes under `services/api/alembic/versions` lists only
      `20260721_0007_query_conversations.py`.
- [ ] Alembic head is exactly `20260721_0007`; targeted upgrade/backfill/downgrade/re-upgrade
      tests pass against disposable PostgreSQL.
- [ ] Migration and ORM composite keys, FK, nullability, and indexes agree.
- [ ] All direct baseline `QueryRunModel` fixtures have scoped conversations.
- [ ] Python/API tests, full Web Vitest, typecheck, mypy, Ruff, generated-contract check, and
      `git diff --check` pass.
- [ ] Existing QA Playwright scenario reports exactly 4 passed across the configured projects.
- [ ] OpenAPI JSON and generated TypeScript contain conversation contracts but no new note
      workflow contracts.
- [ ] No Python contract, Notes UI, note schema/model/service, capability, runner, export,
      cleanup, ETA, `.idea`, or AIWF file is modified.
- [ ] One clean commit exists on `codex/query-conversation-continuity`; nothing was pushed.

## STOP conditions

Stop and report; do not improvise if any condition is true:

- `codex/workflow-checkpoint` is absent, not an immutable commit, not descended from the verified
  `origin/main`, or does not contain the expected 0007/QAPage symbols.
- `origin/main` changed an in-scope path since `856cb1d` and the plan excerpts no longer match.
- Migration 0007 cannot upgrade a database whose head is 0006 without adding/importing
  `7102eb21ee91`, `20260722_0008`, note-version, batch, export, or cleanup schema.
- Alembic reports more than one head or any head other than 0007 in the clean slice.
- ORM metadata requires importing note-workflow models for 0007 to run.
- QAPage or its tests require the unfinished Notes UI, `note_workflow` capability, batch types,
  or the checkpoint change to `apps/web/src/test/render.tsx`.
- Clean OpenAPI generation emits new note-batch/version/export schemas or changes legacy notes
  contracts for reasons unrelated to the conversation endpoints.
- Applying a reviewed whole-file extraction introduces any forbidden scope term or path.
- A verification command still fails after one focused correction within the allowlisted files.
- Fixing a failure appears to require touching any explicitly out-of-scope file.
- The final staged path list contains any path not enumerated in Scope.

## Maintenance notes

- Future note-workflow branches must rebase onto this slice rather than reintroduce 0007 or
  overwrite its migration head test.
- Review the course row lock and composite principal/course FK carefully; weakening either can
  create duplicate implicit conversations or cross-scope associations.
- Conversation context is usability context, never evidence. Any later prompt/retrieval change
  must preserve the explicit trust boundary and current-source validation.
- OpenAPI and generated TypeScript are generated artifacts. Future schema changes must update the
  FastAPI source and regenerate; do not hand-edit either artifact.
- The Playwright mock is stateful per page. Keep its state inside `installMockApi()` so tests do
  not leak conversations between browser projects.
