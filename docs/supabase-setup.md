# Supabase setup

Phase 2A created the database foundation. Phase 2B now replaces browser
Firebase Authentication and protects the existing backend routes, but it still
does not migrate historical Firebase meeting/action data. See
[phase2b-auth.md](phase2b-auth.md) for the Auth architecture, route matrix,
rate limits, hosted-dashboard status, verification, and remaining manual work.

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

## Database model

- `profiles`: one application profile per `auth.users` identity. A protected
  Auth trigger creates the row; no password data is duplicated.
- `meeting_records`: user-owned transcript/source, summary, chosen options, AI
  metadata, and an immutable JSON snapshot of extracted actions.
- `action_items`: mutable manual or AI-generated tasks. A composite foreign key
  prevents an action from referencing another user's meeting.
- `reminder_deliveries`: backend-only reminder attempts and idempotency/audit
  records.

RLS is enabled on all four public tables. Authenticated users can read and
update only their own profile and can create, read, update, and delete only
their own meetings and actions. Anonymous clients receive no table grants.
`reminder_deliveries` has no browser policy or browser grant; it is reserved for
trusted backend use. The service-role key bypasses RLS and must remain on the
server.

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
