# Phase 2D: secure reminder delivery

Phase 2D enables manual and scheduled action reminders without trusting browser
task data or a static scheduler bearer secret. It adds a durable database state
machine, QStash signature verification, structured Brevo outcomes, and
single-use action-status links. The AI and speech providers are unchanged.

## Trust boundaries and routes

- `POST /action-items/{action_item_id}/reminders` requires a verified Supabase
  user, the shared reminder rate limit, an empty body, and a UUID
  `Idempotency-Key`. The backend derives the user from the JWT and reads the
  action, recipient, and content through service-role-only database functions.
  Missing and cross-user action IDs both return the same `404`.
- `POST /internal/reminders/run` accepts only a QStash-signed request. The
  official receiver validates the current or next signing key, expiry,
  not-before time, exact destination URL, and exact raw request-body hash before
  JSON parsing. Browser `Origin` requests, missing/duplicate/malformed
  signatures, altered bodies, wrong URLs, and unavailable verification config
  fail closed. There is no bearer-secret fallback.
- `GET /action-status?token=...` only inspects a token and renders a confirmation
  form. It never changes an action. `POST /action-status` performs the atomic
  transition and redirects with `303` to a URL that contains no token.
- `GET /send-task-reminders`, `POST /send-manual-reminder`, and
  `GET|POST /update-task-status` are retired and return `410`.

The action page calls the manual route with only the action ID in the path and
an idempotency header. It never submits a recipient, owner, status, or reminder
content. The same key is retained after an uncertain network failure.

## Delivery state machine

`reminder_deliveries` is backend-only and records deterministic deduplication,
attempt count, next attempt, claim token, lease, provider-start marker, bounded
failure information, and provider message ID. Its states are:

- `pending`: created but not yet claimed;
- `processing`: owned by one worker until its lease expires;
- `sent`: Brevo returned the documented accepted response;
- `failed`: terminal failure or a retryable failure with `next_attempt_at`;
- `unknown`: the provider may have accepted the email, so blind resend is
  prohibited;
- `cancelled`: the action became ineligible before delivery.

Claims use row locks with `SKIP LOCKED`, a unique dedupe key, and a unique claim
token. Finalization must present the active claim token. An expired lease can be
reclaimed only if the provider call was not marked as started. If a worker is
lost after that marker, the row becomes `unknown` for manual reconciliation.

The QStash worker creates scheduled candidates and also drains due retries from
both scheduled and manual requests. It uses a bounded exponential backoff with
jitter, honours a bounded `Retry-After`, and stops at
`REMINDER_MAX_ATTEMPTS`. Scheduled precedence for the configured logical date
is due today, then starts today, then due tomorrow. Completed or changed actions
are rechecked after the claim and cancelled rather than emailed.

Brevo handling is explicit:

- HTTP `201`: accepted (`sent`), optionally storing the bounded `messageId`;
- connection failure before a response, `408`, `429`, or `5xx`: retryable;
- ordinary `4xx`: terminal failure;
- read/write timeout, transport ambiguity, or an unexpected response: `unknown`.

Ally does not assume Brevo's single-send endpoint has an idempotency guarantee.
This is why ambiguous post-send results are not automatically retried. Logs use
the delivery ID and never include API keys, raw status tokens, recipients,
provider response bodies, or email content.

## Status-token lifecycle

Each delivery attempt generates independent high-entropy tokens for the
currently allowed forward transitions. Only SHA-256 hashes are stored in
`action_status_tokens`; raw tokens exist briefly to construct email links.
Tokens expire after `ACTION_STATUS_LINK_TTL_HOURS`, are single-use, and are
grouped as siblings. A successful transition marks the chosen token used and
revokes its siblings in the same transaction. Retries revoke tokens from the
failed attempt before creating new ones. The database allows only monotonic
`not_started -> in_progress|completed` and `in_progress -> completed`
transitions.

The table has RLS enabled and no `anon` or `authenticated` grants. Its narrow
`SECURITY DEFINER` functions have an empty search path and are executable only
by `service_role`. Status pages are self-contained, set `no-store`,
`no-referrer`, `nosniff`, frame denial, and a restrictive CSP, and load no
third-party resources.

The backend also replaces the token-bearing query string in the shared ASGI
scope after route handling and before the server writes its access log. This
keeps the opaque bearer token out of normal request-line logs as well as
application logs.

## Required environment

Set all values in the backend deployment platform, never in frontend code or
source control:

- `API_PUBLIC_URL`: exact public backend origin, HTTPS in production, with no
  path, query, fragment, or credentials;
- `QSTASH_CURRENT_SIGNING_KEY` and `QSTASH_NEXT_SIGNING_KEY`: the two receiver
  verification keys from the same Upstash QStash project;
- `QSTASH_TOKEN`: schedule-management token, used only by the provisioning
  script and not as inbound request authentication;
- `QSTASH_SCHEDULE_ID`: a stable chosen ID for this one schedule;
- `REMINDER_CRON`: the approved timezone-aware QStash cron expression;
- `REMINDER_TIMEZONE`: the IANA timezone used for action-date semantics,
  currently defaulting to `Europe/London`;
- `REMINDER_BATCH_SIZE`, `REMINDER_MAX_ATTEMPTS`,
  `REMINDER_LEASE_SECONDS`, `REMINDER_RETRY_BASE_SECONDS`, and
  `ACTION_STATUS_LINK_TTL_HOURS`: bounded operational controls;
- `BREVO_API_KEY`, `SENDER_EMAIL`, and `SENDER_NAME`: backend-only delivery
  configuration;
- `RATE_LIMIT_REMINDER_PER_HOUR`: per-user manual reminder limit.

An old `QSTASH_TOKEN` can be reused only if it is still valid and belongs to the
QStash project whose current/next signing keys will verify deliveries. The token
does not replace either signing key. Rotate or revoke it in Upstash if its
provenance is uncertain.

## Safe schedule provisioning

The selected global schedule is 06:00 UK time:

```text
REMINDER_CRON=CRON_TZ=Europe/London 0 6 * * *
REMINDER_TIMEZONE=Europe/London
```

QStash evaluates the `CRON_TZ` IANA timezone, while Ally uses the same timezone
to decide which action calendar date applies. The provisioning script refuses a
cron whose timezone prefix does not match `REMINDER_TIMEZONE`, preventing silent
daylight-saving drift or scheduler/application date disagreement.

After the API code and environment are deployed and `/health` is healthy, run:

```powershell
python scripts/provision_qstash_schedule.py
```

The script lists existing schedules before writing. It exits successfully if
the configured stable ID already has the exact POST destination, cron, method,
and body. It refuses to overwrite a mismatched ID and refuses to create a
second schedule for the same destination. A new schedule posts the fixed body
`{"version":1}` with two QStash delivery retries.

As of 21 September 2026, the selected schedule ID is
`ally-reminders-production`, the backend origin is
`https://ally-back.onrender.com`, and the delivery time is 06:00
`Europe/London`. No schedule was provisioned from this workspace because the
QStash management token and current/next signing keys were not available here.
This deliberately avoids creating an unverified duplicate. Inspect any old
QStash schedules in the Upstash console, then run the idempotent script.

## Deployment order

1. Apply both Phase 2D database migrations and run database lint/tests.
2. Set backend secrets and bounded configuration, including both signing keys.
3. Deploy the backend and frontend together and verify `/health`.
4. Run the idempotent QStash provisioning script once; it will inspect existing
   schedules before creating anything.
5. Send one manual reminder to a disposable action/recipient and verify Brevo,
   confirmation GET, status POST, sibling revocation, and replay rejection.
6. Observe the first scheduled run and reconcile any `unknown` delivery before
   considering a resend.

Never create the schedule before the signed endpoint is deployed. During key
rotation, deploy the Upstash-provided current and next keys together before
promoting the next key. Do not log, paste, or expose either signing key.

## Verification

Run from `ally/`:

```powershell
npm test
python -m compileall -q backend scripts
npx supabase migration list --linked
npx supabase db lint --linked --schema public --level warning --fail-on error
npx supabase db query --linked --file supabase/tests/database/phase2d_security_smoke.sql
npx supabase db query --linked --file supabase/tests/database/phase2d_contract_test.sql
```

The Python suite covers current/next QStash keys, wrong subject, expiry,
not-before, altered and reserialized bodies, missing config, Brevo result
classification, retry/unknown handling, timezone boundaries, HTML escaping,
manual ownership/idempotency, token hashing, GET/POST separation, strict status
pages, and token-free redirects. The frontend suite covers path-only requests
and retry-key retention. The rollback-only SQL suite covers grants, ownership,
deduplication, leases, wrong-worker finalization, manual retry reclamation,
single-use/replay/sibling behavior, and monotonic status transitions.

Docker Desktop's Linux engine is required for `npx supabase test db --linked`.
When it is unavailable, executing the pgTAP file with `db query` still validates
the contract against the linked database. No verification command sends a real
email or invokes QStash.

## Residual operational risk

- A provider call that times out after request transmission is intentionally
  recorded as `unknown`; an operator must reconcile it with Brevo before any
  resend.
- The status POST limiter is a conservative per-process abuse brake. Token
  entropy, expiry, single use, and atomic database rules are the authorization
  boundary; use an edge/WAF rate limit if distributed abuse becomes material.
- Terminal delivery and expired-token cleanup is indexed but not automatically
  destructive. Define and approve a retention period before adding a cleanup
  job so audit records are not removed accidentally.
- Production QStash and Brevo end-to-end delivery remains a deployment smoke
  test because no secrets or real recipient were used during repository tests.
- The application redacts its ASGI access-log scope, but the hosting edge sees
  the initial URL before application code runs. Confirm the production proxy's
  request logs omit or redact `/action-status` query strings and keep any
  unavoidable access-log retention shorter than the token TTL.
