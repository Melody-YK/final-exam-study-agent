# Plan 006: Make the three note templates predictable before generation

> **Executor instructions**: Execute from the reconciled note-regeneration commit below in a clean
> isolated worktree. This plan deliberately touches the same files that were dirty in the primary
> worktree; never copy them again from that workspace. Keep the existing three template enum values;
> do not add a fourth style.
>
> **Drift check (run first)**:
>
> ```bash
> BASE_SHA=84e8d5c688f78214bd95e42e146a0a959d94aca4
> git status --short -- \
>   apps/web/src/features/notes/NotesPage.tsx \
>   apps/web/src/features/notes/NotesPage.test.tsx \
>   apps/web/src/styles/workspace.css \
>   apps/web/src/styles/responsive.css \
>   services/api/src/study_agent/modules/notes/demo_runner.py \
>   services/api/tests/integration/test_note_batch_demo.py \
>   tests/e2e/mockApi.ts \
>   tests/e2e/note-workflow.spec.ts
> ```
>
> Expected: no output in the executor worktree. Any output is a STOP condition. Then compare live
> template rendering and UI text with the facts below, because the plan was authored while a
> separate regeneration slice was in flight.

## Status

- **Priority**: P0
- **Effort**: M
- **Risk**: MED
- **Depends on**: reconciled note-regeneration commit `84e8d5c`
- **Category**: direction / correctness / tests
- **Planned at**: commit `a67dc87`, 2026-07-25; rebased for execution at `84e8d5c`, 2026-07-26
- **Target branch**: `codex/p0-predictable-note-templates`
- **Execution status**: DONE at `59bfc5e3cae7ada8c8cb614136aa576825e6c60b`, independently
  reviewed in the isolated worktree on 2026-07-26

## Why this matters

The UI names three styles but gives only one sentence for each, so learners cannot predict length or
structure until a generation job finishes. The demo runner already applies different limits, but
those limits are implementation constants rather than an explicit template contract. This slice
makes the existing choices honest and testable, with concise exam review as the default.

## Current state

- `NotesPage.tsx:47-55` exposes `exam_focus`, `outline`, and `complete`, with short descriptions only.
- `NotesPage.tsx:327-346` renders three radio options but no structure sample or density indication.
- `demo_runner.py:42-47` caps exam focus at two points per page and 12 per note, and outline at three
  points per page and 30 per note. Complete currently includes every non-empty chunk at lines
  1186-1195, so its output budget is not bounded at the rendering layer.
- `demo_runner.py:1076-1169` changes bullet/numbered/prose formatting but still adds document and
  page headings to all styles, which can make a short note feel longer than its content.
- Existing integration tests at `test_note_batch_demo.py:442+` prove styles differ and content is
  source-derived; extend them instead of replacing them.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Backend focused | `TEST_DATABASE_URL=<disposable-test-db> uv run pytest services/api/tests/integration/test_note_batch_demo.py services/api/tests/integration/test_note_regeneration_runner.py -q` | all selected tests pass |
| Web focused | `npm run test --workspace @study-agent/web -- src/features/notes/NotesPage.test.tsx` | all Notes tests pass |
| E2E | `npm run test:e2e -- tests/e2e/note-workflow.spec.ts --project=chromium-desktop --project=chromium-mobile` | all selected cases pass |
| Python format | `uv run ruff format --check services/api/src/study_agent/modules/notes/demo_runner.py services/api/tests/integration/test_note_batch_demo.py` | exit 0 |
| Python lint | `uv run ruff check services/api/src/study_agent/modules/notes/demo_runner.py services/api/tests/integration/test_note_batch_demo.py` | exit 0 |
| Python types | `MYPYPATH=packages/contracts/python/src:services/api/src:services/worker/src uv run mypy -p study_agent` | success, no issues |
| Web static | `npm run lint && npm run typecheck && npm run build` | all exit 0 |
| Diff hygiene | `git diff --check` | no output |

## Scope

**In scope:**

- `apps/web/src/features/notes/NotesPage.tsx`
- `apps/web/src/features/notes/NotesPage.test.tsx`
- the note-template selector rules in `apps/web/src/styles/workspace.css` and `responsive.css`
- `services/api/src/study_agent/modules/notes/demo_runner.py`
- `services/api/tests/integration/test_note_batch_demo.py`
- `tests/e2e/mockApi.ts` and `tests/e2e/note-workflow.spec.ts`

**Out of scope:**

- New style enum values, API/OpenAPI/generated-schema changes, new LLM prompts, or new model calls.
- Note lifecycle, regeneration control plane, retries, export, DOCX, exact ETA, or active recall.
- Rewriting saved legacy notes or backfilling older content.

## Steps

### Step 1: Define one shared template descriptor per style

In the Web module, expand each existing option with a stable density label, intended use, and a
three-line static structure sample. Suggested product contract:

- `考前速记`: shortest; at most 12 high-value bullets; definitions, conditions, differences, and
  formulas take priority.
- `结构提纲`: medium; at most 30 short numbered points organized by source/page.
- `完整讲义`: longest; source-order prose capped at 40 whole source entries and 12,000 source
  characters, with a visible truncation notice when either limit is reached.

Use concise labels, not tutorial prose. `exam_focus` stays the default. Do not make generation calls
from preview selection.

**Verify**: Web tests assert the three descriptions/samples and that only the selected template's
sample is emphasized without changing the submitted enum value.

### Step 2: Enforce the output budgets in the deterministic runner

Keep current exam and outline caps. Add named constants for a complete-note maximum of 40 entries
and 12,000 source characters. Select the largest source-order prefix of whole non-empty entries that
fits both limits; ingestion already caps an individual chunk at 1,200 characters, so the first
eligible entry always fits. Record a system-generated truncation note in Markdown and AST; never
emit an unlinked partial entry.

For exam focus, remove redundant per-page headings if doing so is required to meet the compact
contract, while preserving document identification and all source-backed AST mappings. Outline and
complete retain navigable structure.

**Verify**: integration tests seed content over every budget and assert maximum source-entry count,
stable ordering, source/AST linkage, explicit complete truncation, and no truncation in a small
complete note.

### Step 3: Keep mocks and E2E semantically aligned

Update the existing dirty-prone mock only after rebasing onto the landed regeneration work. Make its
three generated samples match the descriptors. Extend E2E to inspect template density/sample before
generation, then prove the chosen style changes the generated preview.

**Verify**: focused Web, backend, and Chromium desktop/mobile E2E gates pass.

### Step 4: Run full scoped quality gates

Run Python format/lint/mypy, Web lint/typecheck/build, and diff hygiene. Confirm no API schema or
contract artifact changed.

## Test plan

- Web: all three static descriptors, selected styling, default style, and unchanged request enums.
- Backend: below/at/above each limit, stable ordering, AST/source linkage, and complete truncation.
- E2E: inspect a template before generation, generate two distinct styles, and compare previews.
- Regression: regeneration still preserves the chosen style and existing workflow progress behavior.

## Done criteria

- [x] Three and only three template choices remain; `exam_focus` remains the default.
- [x] Before generation, each option shows intended density, use, and a three-line structure sample.
- [x] All styles have deterministic, tested output limits; complete output is capped at 40 whole
  source entries and 12,000 source characters with an explicit truncation notice.
- [x] Every rendered source entry remains traceable to source and AST data; exam focus omits only
  redundant page headings while retaining document headings, paragraph provenance, and coverage.
- [x] Backend passes 21/21, focused/full Web passes 19/19 and 81/81, desktop/mobile E2E passes 8/8,
  and Ruff, mypy, lint, typecheck, build, and diff hygiene pass.
- [x] OpenAPI, generated TypeScript, and lockfile hashes are unchanged.

## STOP conditions

- Any in-scope file is still dirty from note regeneration.
- The post-regeneration code no longer matches the style/rendering assumptions above.
- Enforcing a budget requires dropping source/AST integrity or changing public enum contracts.
- The measured fixtures make `complete` shorter than `outline`, or the 40-entry/12,000-character
  budget cannot be enforced without partial source entries.
- A fix expands into Provider prompt design, DOCX, history/backfill, or lifecycle work.

## Maintenance notes

Template descriptions and runner limits are one contract and must change together. Reviewers should
focus on source traceability at truncation boundaries. The static preview describes structure and
density; it must never imply that arbitrary generated content is available before the job runs.
