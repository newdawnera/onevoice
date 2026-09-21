# Phase 2B: Supabase Auth and API hardening

Phase 2B replaces browser Firebase Authentication with Supabase Auth and makes
the existing FastAPI AI, transcription, document, and email routes require a
verified Supabase user. Phase 2C subsequently migrated new meeting/action
operations and removed the Firebase runtime; see
[phase2c-data-migration.md](phase2c-data-migration.md). Phase 2D then enabled
reminders behind the controls in
[phase2d-reminders.md](phase2d-reminders.md).

## Architecture

- The browser uses `@supabase/supabase-js` **2.116.0** with the public project
  URL and publishable key. These two values are public configuration, not
  privileged credentials.
- Auth uses PKCE, automatic refresh, and Supabase's standard session storage.
  "Remember me" selects local storage; otherwise the session is kept in session
  storage. Only the short-lived PKCE verifier may temporarily cross tabs, and it
  is deleted after a successful or failed callback.
- The browser sends the current access token as `Authorization: Bearer <token>`
  through the central API client. On a `401`, the client performs one refresh
  and one retry. It does not retry a `403`, loop indefinitely, or manufacture a
  user ID.
- FastAPI verifies access tokens against the project's HTTPS JWKS endpoint.
  The configured algorithm allow-list defaults to `ES256`; `kid`, signature,
  expiry, not-before, issuer, audience, UUID subject, and authenticated role are
  all checked. JWKS data is cached with a bounded TTL and refreshed once for an
  unknown key ID. The legacy `/auth/v1/user` verification path is explicit and
  never treats the publishable key as a JWT signing secret.
- Per-user cost controls are shared across all backend instances through the
  private `api_rate_limits` table and a service-role-only database function.
  Browser roles have no table policy or function execution grant.
- The Supabase service-role key and provider credentials are backend-only. The
  backend fails closed when required verification, rate-limit, or scheduler
  configuration is unavailable.

## Route classification

Public:

- `GET /`
- `GET|HEAD /health`
- `GET /action-status` (read-only token inspection and confirmation page)
- `POST /action-status` (single-use token consumption)
- `GET /action-status/result` (token-free result page)

Authenticated Supabase user:

- `POST /transcribe/`
- `POST /upload-document/`
- `POST /generate-result/`
- `POST /ai-helper`
- `POST /send-email/`
- `POST /send-welcome-email` (authenticated but intentionally returns `410`)
- `POST /action-items/{action_item_id}/reminders` (empty body plus a UUID
  `Idempotency-Key`; all authoritative data is loaded server-side)

Internal machine route:

- `POST /internal/reminders/run` rejects browser-origin requests and requires an
  exact QStash signature over the raw body and configured destination URL.

Disabled legacy routes:

- `GET|POST /update-task-status` returns `410`; an unauthenticated email link
  can no longer mutate task state.
- `GET /send-task-reminders` and `POST /send-manual-reminder` return `410`.
- The legacy `/firebase-config` route was removed in Phase 2C and returns `404`.

## Default API limits

Limits are per authenticated Supabase user and can be adjusted with the
corresponding environment variables:

| Workload | Limits |
| --- | --- |
| AI generation/helper | 10/minute and 200/day |
| Transcription | 3/minute and 30/day |
| Document upload | 10/minute |
| User-requested email | 10/hour |

The limiter returns `429` and `Retry-After` when exhausted and `503` if the
shared store is unavailable. Upload type and size, AI text length, email
recipient count, subject/body length, attachment count/per-file/total size, and
HTML sanitization are enforced separately. Sender identity always comes from
server configuration.

## Hosted Supabase settings observed on 20 September 2026

These dashboard values were inspected read-only and were not changed by this
phase:

- Site URL: `https://ally-vimd.onrender.com`
- Redirect allow list:
  - `http://localhost:5500/**`
  - `http://127.0.0.1:5500/**`
  - `https://ally-vimd.onrender.com/**`
- Email/password signups and email confirmation: enabled.
- Anonymous and social sign-in providers: disabled.
- JWT signing mode: asymmetric `ES256`, published through the project JWKS
  endpoint. No signing-key rotation or migration is needed for this phase.
- Access-token lifetime: 3600 seconds.
- Refresh-token replay detection: enabled, with a 10-second reuse interval.
- Single-session, absolute session-timebox, and inactivity-timeout controls were
  unavailable/disabled on the current free plan; their visible timeout values
  were `0` (never).
- Hosted signup/sign-in rate limit: 30 requests per five minutes.
- Hosted token-refresh rate limit: 150 requests per five minutes.
- Hosted OTP/magic-link verification rate limit: 30 requests per five minutes.
- The Auth email rate-limit value was not confirmed while SMTP remained
  incomplete; review and set it against the verified Brevo allowance.
- CAPTCHA: hCaptcha enabled by the project owner on 2026-09-21. The public
  sitekey is configured in `frontend/scripts/runtimeConfig.js`; the private
  hCaptcha secret remains only in the hosted Supabase Auth configuration.
- Leaked-password protection: disabled.
- Custom SMTP: enabled with Brevo host `smtp-relay.brevo.com`, port `587`,
  sender name `Ally`, and a 60-second per-user interval. The sender email and
  SMTP username appeared blank, and password validity was not inspected.

Therefore custom SMTP is **not confirmed operational**. Before testing signup
or recovery delivery, enter a Brevo-verified sender email, Brevo SMTP login,
and Brevo SMTP key directly in the Supabase dashboard, save, and send a test.
Never paste those secrets into source control, logs, screenshots, or chat.
The HTML files in the repository are application pages; they do not configure
Supabase Auth email templates. Review each Auth email template and its links in
the dashboard separately.

The login, signup, and password-reset request flows render independent hCaptcha
widgets and send their single-use response token to the corresponding Supabase
Auth call. Each widget clears expired tokens and resets after every request.
The hCaptcha secret must never be copied into frontend configuration, source
control, or application logs. Leaked-password protection remains disabled
because it requires a higher Supabase plan.

## Deployment configuration

Set secrets directly in the backend deployment platform and keep them out of
the frontend bundle. Production startup requires valid Supabase URL,
publishable key, service-role key, issuer, audience, and exact HTTPS CORS
origins. Use:

- `APP_ENV=production`
- `SUPABASE_JWT_ISSUER=https://fvltzantpynlbodwczez.supabase.co/auth/v1`
- `SUPABASE_JWT_AUDIENCE=authenticated`
- `SUPABASE_JWT_VERIFICATION_MODE=auto`
- `SUPABASE_JWT_ALGORITHMS=ES256`
- `CORS_ORIGINS=http://localhost:5500,http://127.0.0.1:5500,https://ally-vimd.onrender.com`

Supply `SUPABASE_SERVICE_ROLE_KEY` and the existing AI/transcription/email
provider secrets. Reminder deployment additionally requires the server-only
QStash and Brevo variables documented in
[phase2d-reminders.md](phase2d-reminders.md). The former `SCHEDULER_SECRET` is no
longer used; inbound scheduler authority comes only from QStash signatures.

## Verification

From the repository root:

```powershell
npm test
npx supabase migration list --linked
npx supabase db lint --linked --schema public --level warning --fail-on error
npx supabase db query --linked --file supabase/tests/database/phase2b_rate_limit_smoke.sql
```

The backend test suite covers malformed and duplicate authorization headers,
algorithm and key-ID rejection, invalid and expired claims, valid ES256 access,
JWKS rotation, explicit legacy verification, protected routes,
rate-limit allow/deny/outage behavior, and rejection of
caller-supplied user IDs.

The isolated frontend module suite covers controlled remembered/session-only
storage, PKCE cleanup on success and failure, fixed signup/recovery callbacks,
bearer attachment, FormData and abort-signal preservation, a single refresh
retry after `401`, no refresh after `403`, `429` metadata, logged-out redirects,
safe display-name rendering, and static assertions for generic Auth/recovery
messages and sanitized rich-output hooks. It uses mocks and never creates a
production user or records a real token.

After SMTP and deployment variables are complete, manually test:

1. Signup, email confirmation, normal login, remembered and session-only login,
   logout, reset request, reset callback, and changed-password login.
2. Direct navigation to protected pages while logged out and after token
   expiry/revocation.
3. One successful request to each protected API, followed by missing, malformed,
   expired, and wrong-audience tokens.
4. `429` behavior and `Retry-After`, plus upload/body/attachment limits.
5. Cross-origin rejection from any origin not in the exact allow list.

## Remaining security work and Phase 2C follow-up

- Phase 2C removed Firestore/Firebase runtime code. No repository/workspace
  export was available, so historical meetings/actions were not imported.
- As with any static SPA using Supabase Auth, the active session is readable by
  same-origin JavaScript. The targeted DOM sanitization in this phase reduces
  risk but does not replace the later strict-CSP and dependency-bundling work.
- History and action CRUD are enabled under Phase 2C grants plus RLS. Phase 2D
  adds signed scheduler execution and expiring, replay-controlled status links.
- A strict Content Security Policy is not yet practical because existing pages
  still use runtime Tailwind, several third-party CDN scripts/styles, inline
  styles, and one inline module. Self-host or bundle these assets and remove or
  nonce/hash inline code before adding a restrictive CSP. Configure HSTS at the
  HTTPS hosting layer after confirming all production subdomains support HTTPS.
- Custom SMTP, Auth email-template customization, leaked-password protection,
  deployment secrets, and end-to-end email flows require the manual
  hosted-service work described above. CAPTCHA still requires a production
  browser smoke test after deployment.
