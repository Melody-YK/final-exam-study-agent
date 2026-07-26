# Plan 005: Make the concept graph understandable and actionable

> **Executor instructions**: Execute only after Plan 004 is reviewed. Work from a clean branch and
> keep this slice frontend-only. Run each gate and stop if the API or source-preview boundary must
> expand.
>
> **Drift check (run first)**:
>
> ```bash
> BASE_SHA=a67dc8791707d2ca015aba9167b7ed16ea4fd2c4
> git diff --stat "$BASE_SHA"..HEAD -- \
>   apps/web/src/features/knowledge-graph \
>   apps/web/src/features/qa/QAPage.tsx \
>   apps/web/src/features/qa/QAPage.test.tsx \
>   apps/web/src/app/navigation.tsx \
>   apps/web/src/app/App.test.tsx \
>   tests/e2e/auth-graph.spec.ts \
>   tests/e2e/library.spec.ts \
>   tests/e2e/qa-notes.spec.ts \
>   tests/e2e/visual.spec.ts
> ```
>
> Expected when rebased after Plan 004: no behavioral drift in these paths. STOP if a listed file
> has changed in a way that invalidates the current-state facts below.

## Status

- **Priority**: P0
- **Effort**: M
- **Risk**: LOW
- **Depends on**: `plans/004-source-readiness-actions.md`
- **Category**: correctness / direction / tests
- **Planned at**: commit `a67dc87`, 2026-07-25
- **Target branch**: `codex/p0-actionable-concept-map`
- **Execution status**: DONE at `22e27744e9b933e3de11012995ccb59e34b793ba`, independently
  verified in the isolated worktree on 2026-07-26

## Why this matters

The graph already visualizes documents, concepts, and co-occurrence, but its arrows and raw weights
suggest semantics the backend does not compute. Learners need to understand what a relationship
means, focus on one concept at a time, and carry that concept into QA. This plan improves the
existing deterministic graph; it does not add a graph database, LLM extraction, or a new ontology.

## Current state

- `KnowledgeGraphPage.tsx:96-139` selects a node but always renders the whole layout.
- `NodeDetails` at lines 184-239 shows counts and static occurrence excerpts, with no relationship
  explanation or next action.
- `toFlowEdge` at lines 325-342 adds an arrow to every edge. Backend `service.py:459-475` constructs
  `co_occurs` from unordered concept combinations, so an arrow is incorrect.
- Backend `service.py:426-448` defines `mentions.weight` as a document's concept count, while
  `service.py:459-484` defines `co_occurs.weight` as the number of chunks containing both concepts.
- `QAPage.tsx:241-355` owns a controlled question draft and can start without a conversation. A
  graph handoff must prefill a new-conversation draft and must not call the Provider automatically.
- Existing graph responses already contain all nodes, edges, weights, and occurrence summaries.
  No public contract change is needed.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Focused tests | `npm run test --workspace @study-agent/web -- src/features/knowledge-graph/KnowledgeGraphPage.test.tsx src/features/qa/QAPage.test.tsx src/app/App.test.tsx` | all selected tests pass |
| Full Web | `npm test` | all Web tests pass |
| Lint | `npm run lint` | exit 0, no warnings |
| Typecheck | `npm run typecheck` | exit 0, no errors |
| Build | `npm run build` | exit 0 |
| E2E | `npm run test:e2e -- tests/e2e/auth-graph.spec.ts tests/e2e/library.spec.ts tests/e2e/qa-notes.spec.ts tests/e2e/visual.spec.ts` | all affected workflows pass in all four configured projects |
| Diff hygiene | `git diff --check` | no output |

## Scope

**In scope:**

- `apps/web/src/features/knowledge-graph/KnowledgeGraphPage.tsx`
- `apps/web/src/features/knowledge-graph/KnowledgeGraphPage.test.tsx`
- `apps/web/src/features/knowledge-graph/knowledge-graph.css`
- `apps/web/src/features/qa/QAPage.tsx`
- `apps/web/src/features/qa/QAPage.test.tsx`
- `apps/web/src/app/navigation.tsx`
- `apps/web/src/app/App.test.tsx`
- `tests/e2e/auth-graph.spec.ts`
- `tests/e2e/library.spec.ts`
- `tests/e2e/qa-notes.spec.ts`
- `tests/e2e/visual.spec.ts`

**Out of scope:**

- Python, OpenAPI, generated schema, `knowledgeGraphApi.ts`, and node/edge limits.
- `tests/e2e/mockApi.ts`; its current graph fixture already contains `mentions` and `co_occurs`.
- Opening original pages from an occurrence; that requires Plan 007's authorization contract.
- Neo4j, semantic/causal/prerequisite edges, LLM concept extraction, graph editing, and auto-submitted
  QA requests.

## Steps

### Step 1: Correct relationship semantics

Add pure helpers that describe an edge from its kind and weight. Render a compact legend and a
selected-node relationship list using only visible response edges:

- `contains`: the course contains this document.
- `mentions`: the document contains the concept `N` times.
- `co_occurs`: the two concepts appear together in `N` content chunks.

Keep arrows for `contains` and `mentions`; remove `markerEnd` from `co_occurs` and retain its dashed
line. Rename visible navigation/page text from `知识图谱` to `概念地图` or `概念脉络`, while keeping
the `/graph` route stable.

**Verify**: Vitest asserts all three descriptions and that co-occurrence edges have no arrow marker.

### Step 2: Add one-hop focus

In `GraphExperience`, derive the selected node plus direct neighbors and connecting edges in
`O(nodes + edges)`. Keep the full response in memory. Add an explicit segmented/toggle control for
`全部` versus `仅看关联`; do not hide context merely because a node was clicked. When focused,
either filter unrelated graph elements or mark them dimmed, but maintain stable canvas dimensions
and call `fitView` after the mode changes.

`GraphCanvas` must update when derived nodes change; do not leave `useNodesState(initialNodes)`
stuck on its first render. Preserve drag position behavior where practical and avoid remount loops.

**Verify**: tests select a concept, enable focus, and assert exactly the direct-neighbor set and
visible relation list; switching back restores all nodes.

### Step 3: Hand a concept to a fresh QA draft

For concept nodes, render `围绕此概念提问`. Navigate to `/qa` with a bounded route-state payload
such as `{ suggestedQuestion: '请解释“进程”，并结合课程资料说明它与相关概念的联系。',
startNewConversation: true }`.

In `QAPage`, validate that route state is an object, the suggestion is a non-empty string of at most
2000 characters, and `startNewConversation === true`. Initialize the textarea once, set
`requestedConversationId` to `null`, then clear/replace the navigation state so refresh and back do
not repeatedly reset user input. Never submit automatically.

**Verify**: `QAPage.test.tsx` proves the draft is prefilled, no API mutation is called, the existing
latest conversation is not selected, and malformed/oversized state is ignored.

### Step 4: Add the browser workflow test

Extend `auth-graph.spec.ts` without changing the mock. Select `进程`, inspect the accurate relation
copy, enable local focus, click the QA action, and assert that the composer is prefilled and no answer
appears until the user submits. Update the old graph navigation, heading, and canvas-name assertions
in `library.spec.ts`, `qa-notes.spec.ts`, and `visual.spec.ts` to the same visible `概念地图`
terminology; these files must otherwise receive assertion/locator-only changes. `visual.spec.ts` may
also make the existing `问答` navigation locator exact because Plan 004 adds a second, longer QA
quick-action link on the Library page.

**Verify**: run the four-project E2E command and the full Web gates.

## Test plan

- Pure relationship helpers: contains, mentions, co-occurs, unknown IDs, and undirected ordering.
- Graph component: full/focused modes, direct neighbors only, restore, and selected-node details.
- QA route state: valid draft, malformed state, oversized text, refresh/back behavior, and no submit.
- Browser workflow: graph selection to a fresh unsent QA draft on all configured viewports.

## Done criteria

- [x] Co-occurrence is visibly undirected and accurately explained.
- [x] One-hop focus and restore work on desktop and mobile without changing API limits.
- [x] Concept-to-QA handoff creates only a draft for a new conversation.
- [x] Focused Web passes (38/38), full Web passes (91/91), and lint, typecheck, build, and all
  affected Chromium/WebKit desktop/mobile E2E pass (48/48).
- [x] No backend, OpenAPI, generated schema, mock API, or shared CSS file changed.
- [x] `git diff --check` prints nothing and the final diff is exactly the 11 allowlisted files.

## STOP conditions

- Original-page preview becomes required for acceptance.
- A backend/contract change, Neo4j, LLM extraction, or larger graph response is needed.
- Product behavior requires automatically submitting a Provider request.
- Route state cannot be consumed without joining the most recent persistent conversation.
- The work requires editing dirty `client.ts`, generated schema, `mockApi.ts`, or shared CSS.

## Maintenance notes

Relationship text must remain tied to the deterministic backend definitions. If edge semantics
change later, update the legend and tests in the same commit. The map is a discovery/navigation
surface, not evidence itself; source verification remains a separate, permission-scoped plan.
