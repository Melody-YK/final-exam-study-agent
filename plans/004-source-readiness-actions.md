# Plan 004: Turn document readiness into clear study actions

> **Executor instructions**: Execute this plan in a clean worktree based on the exact commit below.
> Do not edit the user's primary worktree, which contains unrelated note-regeneration changes. Run
> every verification command. If a STOP condition occurs, report it without widening the slice.
>
> **Drift check (run first)**:
>
> ```bash
> BASE_SHA=a67dc8791707d2ca015aba9167b7ed16ea4fd2c4
> test "$(git merge-base HEAD "$BASE_SHA")" = "$BASE_SHA"
> git diff --stat "$BASE_SHA"..HEAD -- \
>   apps/web/src/features/library/LibraryPage.tsx \
>   apps/web/src/features/library/LibraryPage.test.tsx \
>   tests/e2e/library.spec.ts
> ```
>
> Expected before implementation: no output. If any listed file drifted, compare the live code with
> the current-state facts below and STOP on a behavioral mismatch.

## Status

- **Priority**: P0
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: direction / UX / tests
- **Planned at**: commit `a67dc87`, 2026-07-25
- **Target branch**: `codex/p0-source-readiness-actions`
- **Execution status**: DONE at `d5930873a4e0cde5bf590fddbdc2a91333e4604e`, independently
  reconciled in the isolated worktree on 2026-07-25

## Why this matters

The Library already knows which documents are approved, indexed, and usable, but a learner sees a
technical table and must guess whether to open QA, Notes, or the graph. This slice adds a compact
readiness summary and contextual next actions using existing data and routes. It creates the first
visible learning-loop improvement without changing Python, OpenAPI, generated TypeScript, or the
in-progress note workflow.

## Current state

- `apps/web/src/features/library/LibraryPage.tsx:16-31` already has `courseId`, capabilities, and the
  full `DocumentRecord[]`; readiness can be derived locally.
- `apps/web/src/features/library/DocumentTable.tsx:116-119` treats an approved failed document as
  retryable, but a ready document has no study action at lines 156-179.
- `apps/web/src/app/WorkspaceShell.tsx:108-113` already exposes stable `/qa`, `/notes`, and `/graph`
  routes.
- A study-ready document must satisfy all of: `status === 'ready'`, `review_status === 'approved'`,
  non-null `active_revision_id`, and `indexable === true`.
- A note-ready source must additionally be `corpus`, have PDF or supported PPTX media type, and not
  be a legacy `.ppt`. Match `isNoteSourceDocument` in `NotesPage.tsx:61-75` without editing or
  importing from that currently dirty file.
- `tests/e2e/mockApi.ts:24-45` and 310-333 already provide one ready PDF plus pending/failed
  documents. Do not edit that dirty mock for this slice.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Install | `npm ci` | exit 0; `package-lock.json` unchanged |
| Focused tests | `npm run test --workspace @study-agent/web -- src/features/library/LibraryPage.test.tsx` | all Library tests pass |
| Web tests | `npm test` | all Web Vitest tests pass |
| Typecheck | `npm run typecheck` | exit 0, no errors |
| Lint | `npm run lint` | exit 0, no warnings |
| Build | `npm run build` | exit 0; the existing large-chunk warning is non-blocking |
| E2E | `npm run test:e2e -- tests/e2e/library.spec.ts --project=chromium-desktop --project=chromium-mobile` | four cases pass |
| Diff hygiene | `git diff --check "$BASE_SHA"` | no output |

## Scope

**In scope, and the only source/test files that may change:**

- `apps/web/src/features/library/LibraryPage.tsx`
- `apps/web/src/features/library/LibraryPage.test.tsx`
- `apps/web/src/features/library/library-actions.css` (create)
- `tests/e2e/library.spec.ts`

**Out of scope:**

- `DocumentTable.tsx`; per-row lifecycle simplification is a later refinement, not required here.
- `NotesPage.tsx`, API clients, generated schema, OpenAPI, Python, and database code.
- `workspace.css`, `responsive.css`, `test/fixtures.ts`, and `tests/e2e/mockApi.ts`, all of which
  overlap unrelated local work.
- Passing selected document IDs between routes, auto-opening the note dialog, auto-submitting a QA
  prompt, or adding a new product feature.

## Git workflow

- Create a clean isolated worktree at `a67dc87` on `codex/p0-source-readiness-actions`.
- Use one commit: `feat: guide learners from ready sources`.
- Do not push, merge, or modify the primary worktree.

## Steps

### Step 1: Derive stable readiness facts

In `LibraryPage.tsx`, add module-private pure helpers for study-ready and note-ready documents. Add a
small summarizer that reports counts for `可学习`, `待审核`, `准备中`, and `需要处理`; review status
takes precedence over parser/index status so a pending or rejected upload is never called ready.

Keep the mapping local and deterministic. Do not create a backend aggregate contract.

**Verify**: add table-driven assertions in `LibraryPage.test.tsx`, then run the focused Vitest command.

### Step 2: Render the readiness action band

Add a `ReadyStudyActions` component below the existing connection status line and import a new
feature-local stylesheet. The band must be a full-width operational row, not nested cards.

- Always show the readiness counts when documents exist.
- When at least one study-ready document exists, expose `查看概念地图` linking to `/graph`.
- Expose `开始问答` linking to `/qa` only when the Provider capability is available.
- Expose `生成复习笔记` linking to `/notes` only when note generation capability is available and at
  least one note-ready source exists.
- Explain unavailable actions with concise status text; do not render a clickable-looking disabled
  link.
- Use Lucide icons already installed in the repo and preserve keyboard focus visibility.

**Verify**: focused Vitest covers exact hrefs, capability gates, no-ready state, and image-only
documents that cannot generate batch notes.

### Step 3: Cover the visible route handoff

Update `tests/e2e/library.spec.ts` using the existing mock only. In the no-provider test, assert the
readiness summary, concept-map and note actions, and the absence of a QA quick action. Follow the
concept-map action and assert the graph page loads, then return to Library before exercising retry
and deletion.

**Verify**: run the Chromium desktop/mobile E2E command; all four cases pass.

### Step 4: Run the slice gates

Run full Web tests, lint, typecheck, build, and diff hygiene. Inspect `git status --short` and confirm
only the four allowlisted paths changed.

## Test plan

- Ready, approved, active, indexable PDF: summary and all available-capability actions appear.
- Any one readiness predicate false: the document does not increment the ready count.
- Pending review and failed processing produce distinct summary counts.
- Provider unavailable removes only the QA quick action.
- Note workflow unavailable or image-only input removes only the note quick action.
- Desktop and mobile E2E prove the concept-map route handoff and existing Library mutations.

## Done criteria

- [x] Focused Library Vitest passes (10/10) and full Web Vitest passes (82/82).
- [x] Chromium desktop/mobile Library E2E passes (4/4) with no `mockApi.ts` change.
- [x] Web lint, typecheck, and build exit 0; the existing Vite chunk-size advisory remains
  non-blocking.
- [x] No Python/OpenAPI/generated-contract change exists.
- [x] `git diff --check "$BASE_SHA"` prints nothing.
- [x] Only the four in-scope paths differ from `a67dc87`.

## STOP conditions

- A route must carry selected document IDs or auto-open/auto-submit another workflow.
- Readiness cannot be expressed from the existing `DocumentRecord` and capability fields.
- Implementing the UI requires editing shared dirty CSS, `NotesPage.tsx`, or `mockApi.ts`.
- A test reveals that note-source eligibility differs from `NotesPage.tsx:61-75`.
- A verification command still fails after two scoped correction attempts.

## Maintenance notes

Keep readiness predicates aligned with note eligibility when the note-regeneration work lands. A
later per-row lifecycle refinement should reuse the same precedence rules rather than introducing a
second mapping. Do not turn this band into a dashboard or add more learning modes.
