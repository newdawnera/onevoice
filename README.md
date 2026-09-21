# onevoice
OneVoice is a free smart multilingual system for translation and role-based summarization  - with the aim to bridge the language barrier gap between teams.

## Supabase

Phase 2A establishes the secure Postgres foundation. Phase 2B replaces browser
Firebase Authentication with Supabase Auth, verifies Supabase JWTs in FastAPI,
protects cost-bearing routes, and adds shared per-user rate limits. Phase 2C
moves meeting history and action items to owner-scoped Supabase rows, adds an
atomic idempotent meeting-save RPC and secure Realtime subscriptions, and
removes Firebase runtime dependencies. Phase 2D adds QStash-signed reminder
execution, durable idempotent Brevo delivery, and expiring single-use status
links. See
[docs/supabase-setup.md](docs/supabase-setup.md) for database setup and
[docs/phase2b-auth.md](docs/phase2b-auth.md) for the Auth architecture,
deployment checklist, dashboard status, tests, and residual risks. The Phase 2C
data/grants/RLS design is in
[docs/phase2c-data-migration.md](docs/phase2c-data-migration.md), and the Phase
2D trust boundaries, state machine, schedule runbook, and deployment order are
in [docs/phase2d-reminders.md](docs/phase2d-reminders.md).

No Firebase export was found, so historical Firestore records were not imported.
New history, action, and reminder operations use Supabase. The old unsigned
status route and static scheduler/manual reminder routes remain disabled.
