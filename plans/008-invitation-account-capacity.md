# Plan 008: Enforce an explicit invitation account capacity

> **Executor instructions**: Implement a bounded active-account capacity inside the existing
> PostgreSQL transaction/lock model. Preserve first-admin bootstrap and one-time invite secrecy. Do
> not introduce an external queue, autoscaling, billing, or a general quota system.
>
> **Drift check (run first)**:
>
> ```bash
> BASE_SHA=a67dc8791707d2ca015aba9167b7ed16ea4fd2c4
> git diff --stat "$BASE_SHA"..HEAD -- \
>   services/api/src/study_agent/config.py \
>   services/api/src/study_agent/modules/auth/service.py \
>   services/api/src/study_agent/api/routers/auth.py \
>   services/api/src/study_agent/api/schemas/auth.py \
>   apps/web/src/features/admin/AdminUsersPage.tsx
> ```
>
> Expected: no behavioral drift in account registration, invitation creation, account activation,
> or diagnostics. STOP and revise the plan if those state transitions changed.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MED
- **Depends on**: none; execute after P0 for product sequencing
- **Category**: correctness / resource protection / API / tests
- **Planned at**: commit `a67dc87`, 2026-07-25
- **Target branch**: `codex/p1-invitation-account-capacity`
- **Execution status**: DONE, verified on `codex/p1-invitation-account-capacity` on 2026-07-26

## Why this matters

Invitation-only registration slows growth but does not bound it: an administrator can issue more
usable invitations than a small 2-core/2-GB deployment should accept. Capacity must be enforced in
the same transaction as registration and invitation issuance so concurrent requests cannot
oversubscribe seats. This is a local operational guard, not a production scaling claim.

## Current state

- `AccountService.register` at `service.py:155-165` takes a PostgreSQL advisory transaction lock,
  detects the first account, and validates an invitation, but never checks capacity.
- `create_invitation` at lines 385-409 creates an invitation without the registration lock or a
  seat check.
- Reactivating a suspended account at lines 366-370 does not reserve a seat.
- `Settings` has request and note concurrency limits but no account capacity (`config.py:83-139`).
- Diagnostics return account totals but no limit/available-seat fact (`schemas/auth.py:101-123`).
- `AdminUsersPage.tsx` lets an admin create invitations without showing remaining capacity.

## Capacity semantics

- Add `active_account_capacity`, configured by environment through the existing Settings mechanism,
  with a conservative local default of `10`, minimum `1`, and a documented upper validation bound.
- A seat is occupied by an account whose status is `active`. Suspending an account frees a seat;
  reactivation consumes one and must be checked atomically.
- First-account bootstrap is allowed when capacity is at least one and creates the admin as today.
- Available, unexpired invitations reserve seats. Enforce
  `active accounts + available invitations < capacity` when creating a new invitation. Used,
  revoked, and expired invitations do not reserve seats.
- Registration consumes its invitation's reserved seat atomically, so the active+available total
  does not temporarily exceed capacity.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Auth integration | `TEST_DATABASE_URL=<disposable-test-db> uv run pytest services/api/tests/integration/test_auth_api.py -q` | all selected tests pass |
| Contract | `uv run pytest services/api/tests/contract/test_openapi.py -q && npm run generate:api` | contract and generation pass |
| Web focused | `npm run test --workspace @study-agent/web -- src/features/admin/AdminUsersPage.test.tsx src/api/client.test.ts` | all selected tests pass |
| Web static | `npm run typecheck && npm run lint` | both exit 0 |
| Python static | `MYPYPATH=packages/contracts/python/src:services/api/src:services/worker/src uv run mypy -p study_agent` | success, no issues |
| E2E | `npm run test:e2e -- tests/e2e/auth-graph.spec.ts --project=chromium-desktop` | selected browser tests pass |
| Diff hygiene | `git diff --check` | no output |

## Scope

**Backend and contract:**

- `services/api/src/study_agent/config.py` and `.env.example`
- `services/api/src/study_agent/modules/auth/service.py`
- `services/api/src/study_agent/api/errors.py` if a dedicated problem code is added
- `services/api/src/study_agent/api/routers/auth.py`
- `services/api/src/study_agent/api/schemas/auth.py`
- `services/api/tests/integration/test_auth_api.py` and focused config/contract tests
- OpenAPI JSON and generated TypeScript via the generator only

**Web:**

- `apps/web/src/api/client.ts`, `api/types.ts`, generated schema, and client tests
- `apps/web/src/features/admin/AdminUsersPage.tsx` and tests
- `tests/e2e/mockApi.ts` and `tests/e2e/auth-graph.spec.ts` only for capacity UI behavior

**Out of scope:** account deletion, storage quotas, per-user document limits, concurrent-session
limits, job queues, autoscaling, billing, waitlists, email invites, or production load claims.

## Steps

### Step 1: Add validated capacity configuration

Add `active_account_capacity` to Settings and `.env.example` with a short comment explaining active
seats and invitation reservation. Add settings tests for default, override, zero, and above-bound
values. Do not hardcode server-specific environment values into source.

**Verify**: focused config tests pass and `.env.example` contains no secret value.

### Step 2: Centralize seat accounting under the registration lock

Add a private helper that, in the caller's transaction, counts active accounts and available
invitations at `now`. Reuse the existing advisory lock name for registration, invitation creation,
and suspended-to-active transitions so all seat-changing paths serialize.

- Registration validates the invitation, confirms capacity accounting, creates the active account,
  and marks the invitation used in one transaction.
- Invitation creation refuses when no unreserved seat remains.
- Reactivation refuses when active seats are full; suspension remains allowed.
- Duplicate email and invalid-invitation behavior must not leak capacity details to unauthenticated
  callers. Return a stable capacity error only after a valid invitation is established.

Use a dedicated service error and ProblemDetails code if that produces a clearer admin/user message;
do not overload a raw database exception.

**Verify**: PostgreSQL integration tests cover last seat, one-too-many invite, expired/revoked invite
not reserving, registration consuming a reservation, suspension/reactivation, first admin, and two
concurrent invitation/registration attempts with exactly one winner.

### Step 3: Expose capacity to administrators

Extend admin diagnostics with `account_capacity` and `available_account_seats`. Keep existing totals
backward compatible. Regenerate OpenAPI and TypeScript, then show `active / capacity` and remaining
seats in the invitation tab. Disable `创建邀请码` at zero seats and display the server's capacity
ProblemDetails if a race still loses.

The UI is informational; the server remains authoritative.

**Verify**: Web tests cover positive capacity, zero-seat disabled state, and a 409 race response.

### Step 4: Run cross-layer gates

With an explicitly disposable test database whose name contains `test`:

```bash
TEST_DATABASE_URL=<disposable-test-db> uv run pytest services/api/tests/integration/test_auth_api.py -q
uv run pytest services/api/tests/contract/test_openapi.py -q
npm run generate:api
npm run test --workspace @study-agent/web -- src/features/admin/AdminUsersPage.test.tsx src/api/client.test.ts
npm run typecheck
npm run lint
MYPYPATH=packages/contracts/python/src:services/api/src:services/worker/src uv run mypy -p study_agent
uv run ruff format --check services/api/src/study_agent services/api/tests
uv run ruff check services/api/src/study_agent services/api/tests
npm run test:e2e -- tests/e2e/auth-graph.spec.ts --project=chromium-desktop
git diff --check
```

Run API generation twice and confirm the second run produces no generated diff.

## Test plan

- Settings: default/override/min/max validation.
- Service/API: first admin, final seat, invite reservation, expiry/revoke, suspend/reactivate, and
  stable error mapping after a valid invitation.
- PostgreSQL concurrency: simultaneous seat-changing requests cannot both exceed capacity.
- Web/E2E: remaining-seat display, zero-seat disabled action, and server race error.

## Done criteria

- [x] Active accounts plus usable reserved invitations cannot exceed configured capacity.
- [x] Registration, invite creation, and reactivation are serialized and race-tested on PostgreSQL.
- [x] First-admin bootstrap and existing invite secrecy/one-time behavior remain intact.
- [x] Admin UI shows capacity and cannot deliberately create an invite at zero seats.
- [x] API/OpenAPI/TypeScript contracts agree and generation is deterministic.
- [x] Focused backend/Web/E2E, mypy, Ruff, lint, typecheck, and diff hygiene pass.

## STOP conditions

- Capacity semantics require deleting accounts or data to recover a seat.
- PostgreSQL advisory locking cannot cover every active-seat transition in one transaction.
- A valid invitation would reveal sensitive account counts to an unauthenticated user.
- The change expands into concurrent-session throttling, job scheduling, billing, or autoscaling.
- The dirty note-regeneration contract cannot be separated from generated artifact changes; rebase
  after it lands rather than copying generated files wholesale.

## Maintenance notes

Capacity is a coarse admission guard, not a concurrency benchmark. Any future account status must
declare whether it occupies a seat. Review generated contract diffs by schema content, not file size,
and keep the server-side lock/check authoritative over UI state.
