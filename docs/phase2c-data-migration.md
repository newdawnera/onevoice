# Phase 2C: application data migration

Phase 2C replaces the active Firestore meeting-history and action-item paths
with Supabase Postgres. New Ally data is stored under the authenticated
Supabase user's UUID. No Firebase export was present in the repository or its
workspace, so no historical records were imported or reconstructed.

## Data mapping

| Previous Firebase data | Supabase data |
| --- | --- |
| Firebase Auth user | `auth.users` |
| `users/{uid}` | `profiles` |
| `users/{uid}/history/{historyId}` | `meeting_records` |
| `users/{uid}/actionLogs/{taskId}` | `action_items` |
| Reminder attempts | `reminder_deliveries` (server-only; enabled securely in Phase 2D) |

Uploaded audio, video, and documents are not stored. A meeting row contains
the extracted source text/approved rich HTML, summary text/approved rich HTML,
source metadata, selected role/language, backend-supplied AI metadata, and an
immutable normalized action snapshot.

## Grants and RLS

RLS is enabled on `profiles`, `meeting_records`, `action_items`, and
`reminder_deliveries`. RLS and SQL grants are both required: a policy decides
which rows a role may access, while the grant decides which operation/columns
the role may attempt.

- `anon` has no application-table access and cannot execute the meeting-save
  function.
- `profiles`: `authenticated` can select its own row and update only
  `display_name`.
- `meeting_records`: `authenticated` has owner-scoped `SELECT` only. Direct
  insert/update/delete is not granted.
- `action_items`: `authenticated` can select/delete its own rows, insert only
  the manual-action columns, and update only title, assignee/email, status, and
  dates. RLS derives the permitted owner from `auth.uid()`. A trigger also
  protects ID, owner, meeting link, provenance, and creation time.
- `reminder_deliveries`: no `anon` or `authenticated` grant or policy.

The browser contains only the project URL and publishable key. It never
contains the service-role key. Routine reads and action CRUD use the existing
authenticated Supabase browser client and remain subject to RLS.

## Atomic meeting save and idempotency

> Phase 2E update: this describes the original Phase 2C browser flow for
> historical context. Browser execution of `save_meeting_with_actions` is now
> revoked. New AI generations use the backend-only durable ledger and
> `persist_ai_generation`, and every generated action starts pending review.
> See `docs/phase2e-ai-provider.md`.

`public.save_meeting_with_actions(...)` atomically creates one meeting, zero or
more `ai_generated` actions, and the meeting's immutable action snapshot. It
does not accept an owner parameter; both meeting and action owners are derived
exclusively from `auth.uid()`.

The function is `SECURITY DEFINER` because browser clients deliberately lack a
meeting insert grant and may not directly create AI-provenance actions. Its
scope is narrow: execution is revoked from `public` and `anon`, granted only to
`authenticated`, `search_path` is empty, all objects are schema-qualified, no
dynamic SQL is used, and inputs are bounded and normalized.

The browser creates one `crypto.randomUUID()` per generated result. The value
is stored in `meeting_records.client_request_id`, with a unique constraint on
`(user_id, client_request_id)`. A retry returns the caller's existing meeting
ID and does not duplicate actions. The same UUID can be used by two different
users without collision or disclosure.

Action snapshot JSON uses frontend-compatible camelCase keys:

- `title`
- `assignee`
- `assigneeEmail`
- `startDate`
- `deadline`

Relational columns remain snake_case. Unknown model fields are removed by the
browser normalizer and rejected by the database boundary. The old
`Generated from Summary` status is represented as `source = 'ai_generated'`
plus `status = 'not_started'`.

## Frontend operations

`frontend/scripts/dataService.js` is the single query boundary for profiles,
meetings, actions, and Realtime subscriptions.

- Home generates the summary, extracts and normalizes actions, then makes one
  RPC call. A failed save leaves the generated result visible and offers a
  retry using the same request UUID. Extraction failure can save an empty
  snapshot and reports that outcome.
- History requests 20 rows at a time ordered by `created_at DESC`, supports
  **Load more**, and states that search covers loaded rows only. Stored source,
  summary, and snapshot fields are rendered through `textContent`, not HTML.
- Actions are mapped centrally between snake_case rows and frontend models.
  Manual create, editable-field update, owner-filtered delete, search, filters,
  date validation, workflow status, and AI/manual provenance are supported.
- PostgreSQL `date` values stay as `YYYY-MM-DD` strings, so overdue comparisons
  cannot shift a day through UTC conversion. `timestamptz` event values are
  parsed and displayed in the browser's local timezone.

The database enforces practical limits: 500,000 characters for source text or
HTML, 250,000 for summary text or HTML, 100 extracted actions, 500 characters
per action title, and smaller documented metadata/email limits. These protect
the database without imposing an unusually small transcript ceiling.

## Realtime

Only `meeting_records` and `action_items` were added to the
`supabase_realtime` publication. The migration tolerates either table already
being present.

- Channels use the authenticated shared client and `user_id=eq.<current user>`
  filters.
- RLS remains the authorization boundary; the client filter is defense in
  depth, not authority.
- History consumes inserts into an ID-keyed map, preventing an initial-query /
  INSERT-event race from duplicating a card.
- Action INSERT/UPDATE/DELETE events trigger a debounced owner-scoped refetch.
- Reinitialization removes an earlier channel. Page teardown, Auth sign-out,
  and explicit logout remove all channels.
- The UI reports disconnected Realtime separately from query errors; users can
  still reload to refresh data.

No public Broadcast or Presence channel is used.

## Firebase and historical data

The frontend Firebase module and CDN imports, backend Firebase Admin imports,
initialization helper, dependency, environment placeholders, deleted-project
configuration, Firestore paths, and `.uid` compatibility code are removed.
`GET /firebase-config` no longer exists.

A filename/content search of the repository and containing workspace found no
Firestore JSON export, Auth export, CSV, storage export, or downloaded backup.
Nothing was fabricated, and no import script was created without a real source
format. If an export is later recovered, stop before importing and create a
separate identity-mapping plan; do not map owners by email alone.

Historical mentions in Phase 2A/2B migrations, tests, and documentation are
retained as migration history, not active runtime dependencies. The generic,
disabled Firebase third-party-provider example in `supabase/config.toml` is a
Supabase CLI template setting and contains no Ally Firebase project ID.

## Phase 2D reminder follow-up

Phase 2D subsequently enabled reminders through an authenticated, path-only
manual endpoint and a QStash-signed POST worker. The browser still sends no task,
owner, recipient, or status authority. Raw status tokens are not stored, GET does
not mutate state, and the legacy unsigned routes remain disabled with `410`.
See [phase2d-reminders.md](phase2d-reminders.md) for the current design.

## Verification

Run from `ally/`:

```powershell
npm test
npx supabase migration list --linked
npx supabase db lint --linked --schema public --level warning --fail-on error
npx supabase db query --linked --file supabase/tests/database/phase2c_security_smoke.sql
npx supabase test db --linked supabase/tests/database/phase2c_contract_test.sql
```

The security smoke test and pgTAP file are transactionally rolled back and do
not leave test users or application rows. `supabase test db` uses a Docker
`pg_prove` runner; start Docker Desktop's Linux engine before using that wrapper
on Windows. When Docker is unavailable, the pgTAP SQL can still be executed
through `supabase db query --linked --file ...`, while the comprehensive smoke
test remains the authoritative allow/deny and atomicity check.

The official linked wrapper was rerun with Docker Desktop on 2026-09-21 and
passed all 14 contract assertions (`Files=1, Tests=14, Result: PASS`). The test
uses a transaction-local `postgres` role because the CLI's temporary
`cli_login_postgres` linked-test role does not have `USAGE` on the hosted
`extensions` schema containing pgTAP. The role change and all test effects are
removed by the final rollback.

Manual verification should use two disposable Supabase users and a local HTTP
server (never `file://`) to confirm cross-user isolation, one-save/one-meeting
behavior, history pagination/search wording, action CRUD/filters, Realtime
  insert/update/delete, logout cleanup, and the reminder UI. Remove any
temporary users/rows after the test.

## Residual risks and next phase

- Historical Firebase data cannot be recovered unless a real export is found.
- Action listing is bounded to the newest 500 items; add action pagination
  before users can reasonably exceed that amount.
- Realtime Postgres Changes is suitable for the current scale but should be
  revisited if connection/change volume grows materially.
- The SPA still has the Phase 2B same-origin script/CSP risks and the hosted
  SMTP/leaked-password/deployment work recorded in
  [phase2b-auth.md](phase2b-auth.md).
- Reminder delivery now depends on the Phase 2D deployment variables and one
  canonical QStash schedule described in
  [phase2d-reminders.md](phase2d-reminders.md).

Phase 2D completed the planned reminder-security work. AI-provider and
speech-provider migration remains outside this phase.
