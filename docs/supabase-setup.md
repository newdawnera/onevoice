# Supabase setup

Phase 2A created the database foundation. Phase 2B replaced browser Firebase
Authentication and protected the backend routes. Phase 2C stores new meeting
history and action items in Supabase and removes Firebase runtime code. Phase
2D adds the durable reminder state machine and hashed status tokens.
No historical Firebase export was available to import. See
[phase2b-auth.md](phase2b-auth.md) for the Auth architecture, route matrix,
rate limits, hosted-dashboard status, verification, and remaining manual work,
and [phase2c-data-migration.md](phase2c-data-migration.md) for the application
data, grants, RLS, atomicity, and Realtime design. See
[phase2d-reminders.md](phase2d-reminders.md) for reminder delivery, QStash,
status-link security, and deployment operations.

## CLI workflow

Run commands from the repository root (`ally/`). The CLI is installed as a
project development dependency, so invoke it through `npx`.

```powershell
# Confirm which cloud project is linked. The expected project is AllyUpdated.
npx supabase projects list

# Compare local and remote migration history.
npx supabase migration list --linked

# Preview pending migrations before making a remote change.
npx supabase db push --linked --dry-run

# Apply reviewed migrations.
npx supabase db push --linked

# Check the deployed public schema for database errors.
npx supabase db lint --linked --schema public --level warning --fail-on error

# Run the rollback-only ownership and Auth-trigger smoke test.
npx supabase db query --linked --file supabase/tests/database/phase2a_security_smoke.sql

# Run the rollback-only shared rate-limit smoke test.
npx supabase db query --linked --file supabase/tests/database/phase2b_rate_limit_smoke.sql

# Run the rollback-only Phase 2C allow/deny and atomic-save test.
npx supabase db query --linked --file supabase/tests/database/phase2c_security_smoke.sql

# Run the Phase 2C pgTAP contract through the supported test wrapper.
# Docker Desktop's Linux engine must be running on Windows.
npx supabase test db --linked supabase/tests/database/phase2c_contract_test.sql

# Run the rollback-only Phase 2D state-machine/security smoke test.
npx supabase db query --linked --file supabase/tests/database/phase2d_security_smoke.sql

# Validate the Phase 2D schema/grant contract (or use `supabase test db` with Docker).
npx supabase db query --linked --file supabase/tests/database/phase2d_contract_test.sql
```

Do not run `supabase db reset --linked`; it destroys remote data.

## Environment variables

Copy `.env.example` to an ignored local environment file and supply values
through the deployment platform. Never commit real values.

- `SUPABASE_URL`: cloud project URL. This may eventually be public runtime
  configuration for the browser.
- `SUPABASE_PUBLISHABLE_KEY`: browser-appropriate project key used by the
  Supabase Auth client and by the backend's explicit legacy verification path.
- `SUPABASE_SERVICE_ROLE_KEY`: server-only administrative credential. Never
  expose it in frontend JavaScript, HTML, logs, screenshots, or chat.
- `SUPABASE_JWT_ISSUER`: issuer used by the backend when validating Supabase
  access tokens.
- `SUPABASE_JWT_AUDIENCE`: expected access-token audience (`authenticated`).
- `API_PUBLIC_URL`, QStash verification/schedule values, Brevo delivery values,
  and reminder bounds are documented in
  [phase2d-reminders.md](phase2d-reminders.md). Keep all secret values server-only.

## Database model

- `profiles`: one application profile per `auth.users` identity. A protected
  Auth trigger creates the row; no password data is duplicated.
- `meeting_records`: user-owned transcript/source, summary, chosen options, AI
  metadata, and an immutable JSON snapshot of extracted actions.
- `action_items`: mutable manual or AI-generated tasks. A composite foreign key
  prevents an action from referencing another user's meeting.
- `reminder_deliveries`: backend-only reminder attempts and idempotency/audit
  records.
- `action_status_tokens`: hashes of expiring, single-use status links; raw tokens
  are never stored.

RLS is enabled on all four public tables. Authenticated users can read and
update only the safe field on their own profile, read only their own meetings,
and create/update/delete only their own actions through restricted columns.
Meeting creation uses the authenticated-only atomic RPC described in the Phase
2C guide. Anonymous clients receive no table grants.
`reminder_deliveries` and `action_status_tokens` have no browser policy or
browser grant; both are reserved for narrow service-role functions. The
service-role key bypasses RLS and must remain on the server.

## Manual Supabase dashboard actions

These hosted Auth settings are intentionally not hard-coded into the database
migration. Their current observed status and exact remaining work are recorded
in [phase2b-auth.md](phase2b-auth.md). Before release:

1. Open **Authentication > URL Configuration**.
2. Set **Site URL** to the verified production frontend origin. The repository
   currently references `https://ally-vimd.onrender.com`; confirm that it is
   still the intended production origin before saving it.
3. Keep the local redirect allow-list entries `http://localhost:5500/**` and
   `http://127.0.0.1:5500/**` while port 5500 is used for local static serving.
4. Add `https://ally-vimd.onrender.com/**` if that remains the deployed
   frontend. Otherwise replace it with
   `https://<production-frontend-domain>/**`; do not leave an obsolete domain
   allow-listed.
5. The implemented callback paths are `auth-callback.html` and
   `reset-password.html`. The wildcard origin entries above currently cover
   both. If wildcards are later narrowed, allow these exact local and production
   URLs before deploying the change.
6. Open **Authentication > Providers > Email**. Keep **Confirm Email** enabled
   for the initial production release so an email address must be verified
   before first sign-in. Disabling it implicitly treats addresses as verified.
7. Before inviting external testers, complete Brevo under
   **Authentication > Emails > SMTP Settings**. The sender email and SMTP
   username appeared blank during the Phase 2B inspection, so SMTP is not yet
   confirmed operational. Enter credentials directly in the dashboard; never
   store them in this repository.
8. Review **Authentication > Rate Limits** after custom SMTP is enabled and set
   a limit that matches the verified Brevo sending allowance and expected signup
   volume. Supabase currently starts custom SMTP projects at 30 Auth emails per
   hour, so review this before a public launch.
9. The linked project is in `eu-west-1`. Confirm this satisfies latency and data
   residency requirements before storing production data; changing region later
   requires a project migration.

The frontend now uses explicit same-origin `redirectTo` values. Keep them in
sync with the dashboard allow list whenever the production domain changes.
