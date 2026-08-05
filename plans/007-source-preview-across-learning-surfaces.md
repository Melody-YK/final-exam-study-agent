# Plan 007: Reuse original-page preview for notes and concept occurrences

> **Executor instructions**: This is a P1 cross-layer slice. Execute only after Plans 005 and 006
> are DONE and the note-regeneration work is clean. Preserve query citation URLs and authorization;
> add generic preview support alongside them. Never construct storage URLs in the browser.
>
> **Drift check (run first)**:
>
> ```bash
> BASE_SHA=a67dc8791707d2ca015aba9167b7ed16ea4fd2c4
> git status --short -- \
>   services/api/src/study_agent/api/routers/sources.py \
>   services/api/src/study_agent/modules/answering/sources.py \
>   services/api/src/study_agent/modules/answering/source_tokens.py \
>   services/api/src/study_agent/api/routers/notes.py \
>   apps/web/src/features/source-viewer/SourceViewer.tsx \
>   apps/web/src/features/notes/NotesPage.tsx \
>   apps/web/src/features/knowledge-graph/KnowledgeGraphPage.tsx
> ```
>
> Expected: no output. Then compare the live endpoint and source-viewer contracts with the facts
> below. Any authorization or storage-signing drift is a STOP condition requiring plan revision.

## Status

- **Priority**: P1
- **Effort**: L
- **Risk**: MED
- **Depends on**: Plans 005 and 006; note-regeneration slice reconciled
- **Category**: security / direction / API / tests
- **Planned at**: commit `a67dc87`, 2026-07-25
- **Target branch**: `codex/p1-source-preview-surfaces`
- **Execution status**: DONE, landed in `abe7fcb` and carried forward by the current learning surfaces

## Why this matters

QA is currently the only surface where a learner can move from an answer back to the original page.
Notes and the concept map show quotes and page numbers but stop before verification. A shared,
principal-scoped source-preview contract makes those surfaces trustworthy without exposing raw
object keys or weakening deletion/revision checks.

## Current state

- `SourceViewer.tsx:7-10` accepts `CitationSource`; the renderer actually needs document name,
  locator, quote, bounding boxes, media type, read URL, and expiry, not a query ID.
- `sources.py:111-146` exposes only `GET /queries/{query_id}/citations/{citation_id}` and produces
  local signed content URLs bound to that query/citation pair.
- `answering/sources.py:56-145` resolves a source through answer dependency, owner identity, active
  revision, deletion epoch, and stored-object state. Those protections are mandatory.
- `NotesPage.tsx:612-652` renders note sources as static text even though each source has note ID,
  document/revision/chunk IDs, locator, quote, and availability state.
- Graph occurrences expose document/revision/chunk/page/excerpt but no media type or read URL.
  Frontend code cannot safely fabricate the missing data.

## Proposed contract

Add two owner-scoped metadata routes that return one generic `SourcePreviewResponse`:

- `GET /notes/{note_id}/sources/{source_id}/preview`
- `GET /courses/{course_id}/knowledge-graph/sources/{revision_id}/{chunk_id}/preview`

Add corresponding short-lived local content routes. The metadata response contains a neutral
`source_id` plus the fields currently consumed by `SourceViewer`. Keep the existing
`CitationSourceResponse` and query citation routes backward compatible.

The server must independently prove ownership, active course/document/revision, matching deletion
epoch, available source/chunk, and a persisted original/rendered-page object before signing. A graph
request must prove that the chunk belongs to the course's current active revision; request path IDs
alone are not authorization.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Source integration | `TEST_DATABASE_URL=<disposable-test-db> uv run pytest services/api/tests/integration/test_sources_api.py services/api/tests/integration/test_knowledge_graph.py services/api/tests/integration/test_notes.py -q` | all selected tests pass |
| Contract | `uv run pytest services/api/tests/contract/test_openapi.py -q && npm run generate:api` | contract test passes; generation exits 0 |
| Web | `npm test && npm run typecheck && npm run lint` | all exit 0 |
| E2E | `npm run test:e2e -- tests/e2e/qa-notes.spec.ts tests/e2e/auth-graph.spec.ts` | all configured projects pass |
| Python static | `MYPYPATH=packages/contracts/python/src:services/api/src:services/worker/src uv run mypy -p study_agent && uv run ruff format --check services/api/src/study_agent services/api/tests && uv run ruff check services/api/src/study_agent services/api/tests` | all exit 0 |
| Diff hygiene | `git diff --check` | no output |

## Scope

**Backend/API:**

- `services/api/src/study_agent/modules/answering/sources.py` or a new adjacent
  `modules/sources/preview.py` for shared object/page resolution
- `services/api/src/study_agent/modules/answering/source_tokens.py`
- `services/api/src/study_agent/api/routers/sources.py`
- `services/api/src/study_agent/api/routers/notes.py` only if route ownership stays there
- OpenAPI contract tests and generated artifacts
- focused integration tests for notes, graph sources, signing, deletion, and ownership

**Web:**

- `apps/web/src/features/source-viewer/SourceViewer.tsx` and tests
- `apps/web/src/api/client.ts`, `api/types.ts`, generated schema, and client tests
- `apps/web/src/features/notes/NotesPage.tsx` and tests
- `apps/web/src/features/knowledge-graph/KnowledgeGraphPage.tsx` and tests
- E2E mock and note/graph specs, rebased carefully onto the landed regeneration work

**Out of scope:** public sharing links, downloads, source annotations, document editing, stale-source
recovery, OCR repair, graph semantics, note regeneration, DOCX/export, or long-lived URLs.

## Steps

### Step 1: Extract a neutral preview value without weakening citation access

Introduce a shared internal preview dataclass and object/page resolver. Keep the existing query
service as a principal-scoped lookup that supplies its validated dependency to the resolver. Add
note-source and graph-chunk lookups that independently join through owner/course/document/revision
facts before calling the same resolver.

Do not accept an object key, media type, locator, or deletion epoch from the client.

**Verify**: unit/integration tests cover PDF original objects, PPTX rendered pages, missing rendered
assets, cross-user/course IDs, stale revision, deleted document, changed deletion epoch, unavailable
note source, and absent chunk.

### Step 2: Generalize local read grants with an explicit scope

Version the signer payload so it signs `scope`, `parent_id`, `source_id`, and expiry. Preserve v1
verification for existing query citation URLs during this slice, or keep the query signer unchanged
and add a separate neutral signer. Every content endpoint validates scope and expiry before reading
storage, then repeats or relies on an immutable server-side lookup so revocation/deletion closes
access.

**Verify**: tampered scope/parent/source, expired grants, and grants replayed against a different route
return the same non-enumerating 404 behavior as existing source URLs.

### Step 3: Publish and generate the two preview contracts

Define `SourcePreviewResponse` with `extra='forbid'` semantics and generate OpenAPI and TypeScript.
Add typed client methods. Regeneration must contain only this slice plus already-landed contracts;
review generated diffs for unexpected routes or schemas.

**Verify**:

```bash
uv run pytest services/api/tests/contract/test_openapi.py -q
npm run generate:api
git diff --exit-code -- packages/contracts/openapi/openapi.json apps/web/src/api/generated/schema.ts
```

The final equality command is run after a second generation; it must show deterministic output.

### Step 4: Make SourceViewer neutral and wire notes/graph

Change `SourceViewer` to accept the generic fields it renders. In Notes, only active/available sources
are buttons; stale/unavailable sources remain explanatory text. In the graph, each occurrence gains
an `查看原页` action that fetches the preview lazily. Both surfaces use the existing modal and expose
loading/error state without closing the current context.

**Verify**: component tests assert lazy fetch, correct IDs, stale/unavailable gating, close/reopen,
expired-preview error, and unchanged QA citation behavior.

### Step 5: Run security and workflow gates

Use an explicitly disposable PostgreSQL test database whose name contains `test`:

```bash
TEST_DATABASE_URL=<disposable-test-db> uv run pytest \
  services/api/tests/integration/test_sources_api.py \
  services/api/tests/integration/test_knowledge_graph.py \
  services/api/tests/integration/test_notes.py -q
npm test
npm run typecheck
npm run lint
npm run test:e2e -- tests/e2e/qa-notes.spec.ts tests/e2e/auth-graph.spec.ts
MYPYPATH=packages/contracts/python/src:services/api/src:services/worker/src uv run mypy -p study_agent
uv run ruff format --check services/api/src/study_agent services/api/tests
uv run ruff check services/api/src/study_agent services/api/tests
git diff --check
```

## Test plan

- API happy paths: query citation, note source, graph PDF occurrence, and PPTX rendered page.
- Authorization/invalidation: wrong owner/course, stale revision, deletion epoch, unavailable note
  source, missing chunk/object, expired/tampered/replayed grant.
- Web: lazy fetch, loading/error/close, stale gating, and unchanged QA SourceViewer behavior.
- E2E: notes and graph return to original pages while QA citation preview remains functional.

## Done criteria

- [ ] QA source preview remains backward compatible.
- [ ] Note and graph previews enforce owner, course, active revision, deletion, and availability.
- [ ] No browser response exposes storage object keys.
- [ ] Local content grants are short-lived, scope-bound, and non-replayable across routes.
- [ ] Notes and graph lazily open the shared SourceViewer; stale/unavailable sources cannot open.
- [ ] Contract generation is deterministic and all focused/full gates pass.

## STOP conditions

- Plans 005/006 or the regeneration slice are not cleanly landed.
- A preview can only be implemented by trusting client-provided storage/location facts.
- The shared abstraction would weaken existing query authorization or deletion safety.
- Object storage cannot provide the required persisted original/rendered page.
- The implementation expands into exports, public sharing, annotations, or source repair.

## Maintenance notes

Review every lookup as an IDOR boundary. New learning surfaces should consume the neutral preview
contract rather than inventing another signed-URL path. Keep query citation IDs and generic source
IDs semantically distinct even if both render through the same Web component.
