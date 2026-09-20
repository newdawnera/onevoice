begin;

-- Supabase creates this event-trigger function when automatic RLS is enabled.
-- The DDL event trigger can use it without exposing it as a Data API RPC.
revoke all on function public.rls_auto_enable() from public, anon, authenticated;

commit;
