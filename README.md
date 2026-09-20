# onevoice
OneVoice is a free smart multilingual system for translation and role-based summarization  - with the aim to bridge the language barrier gap between teams.

## Supabase

Phase 2A establishes the secure Postgres foundation. Phase 2B replaces browser
Firebase Authentication with Supabase Auth, verifies Supabase JWTs in FastAPI,
protects cost-bearing routes, and adds shared per-user rate limits. See
[docs/supabase-setup.md](docs/supabase-setup.md) for database setup and
[docs/phase2b-auth.md](docs/phase2b-auth.md) for the Auth architecture,
deployment checklist, dashboard status, tests, and residual risks.

Historical Firestore meeting/action data has not been migrated. History,
actions, and reminders remain intentionally disabled until that separate phase.
