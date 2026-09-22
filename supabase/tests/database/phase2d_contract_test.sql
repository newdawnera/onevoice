begin;

create extension if not exists pgtap with schema extensions;
alter extension pgtap set schema extensions;
set local role postgres;
set local search_path = public, extensions, pg_catalog;

select plan(24);

select has_table('public', 'reminder_deliveries', 'reminder delivery state table exists');
select has_table('public', 'action_status_tokens', 'status token table exists');
select has_column('public', 'reminder_deliveries', 'claim_token', 'claim token exists');
select has_column('public', 'reminder_deliveries', 'lease_expires_at', 'claim lease exists');
select has_column('public', 'reminder_deliveries', 'provider_attempted_at', 'provider attempt marker exists');
select has_column('public', 'reminder_deliveries', 'next_attempt_at', 'retry time exists');
select has_column('public', 'action_status_tokens', 'token_hash', 'only a token hash column exists');
select hasnt_column('public', 'action_status_tokens', 'token', 'raw token column does not exist');
select has_function('public', 'claim_manual_reminder', array['uuid','uuid','uuid','uuid','integer','integer'], 'manual claim RPC exists');
select has_function('public', 'claim_scheduled_reminders', array['date','integer','integer','integer'], 'scheduled claim RPC exists');
select has_function('public', 'prepare_reminder_delivery', array['uuid','uuid','uuid','timestamp with time zone','jsonb'], 'prepare RPC exists');
select has_function('public', 'mark_reminder_send_started', array['uuid','uuid'], 'provider-attempt marker RPC exists');
select has_function('public', 'finalize_reminder_delivery', array['uuid','uuid','text','text','text','text','timestamp with time zone'], 'claim-token finalizer exists');
select has_function('public', 'consume_action_status_token', array['text','timestamp with time zone'], 'atomic token consumer exists');
select ok(
  (select relrowsecurity from pg_class where oid = 'public.action_status_tokens'::regclass),
  'status tokens have RLS enabled'
);
select ok(not has_table_privilege('anon', 'public.action_status_tokens', 'select'), 'anon cannot read token hashes');
select ok(not has_table_privilege('authenticated', 'public.action_status_tokens', 'select'), 'authenticated cannot read token hashes');
select ok(not has_table_privilege('authenticated', 'public.reminder_deliveries', 'update'), 'authenticated cannot claim deliveries');
select ok(
  not has_function_privilege('authenticated', 'public.claim_manual_reminder(uuid,uuid,uuid,uuid,integer,integer)', 'execute'),
  'authenticated cannot execute reminder claims'
);
select ok(
  not has_function_privilege('anon', 'public.consume_action_status_token(text,timestamp with time zone)', 'execute'),
  'anonymous clients cannot consume tokens through Data API RPC'
);
select ok(
  has_function_privilege('service_role', 'public.claim_scheduled_reminders(date,integer,integer,integer)', 'execute'),
  'service role can execute the worker claim function'
);
select ok(
  has_function_privilege('service_role', 'public.consume_action_status_token(text,timestamp with time zone)', 'execute'),
  'service role can execute atomic status-token consumption'
);
select index_is_unique(
  'public',
  'reminder_deliveries',
  'reminder_deliveries_dedupe_key_key',
  'delivery dedupe keys are database-unique'
);
select index_is_unique(
  'public',
  'action_status_tokens',
  'action_status_tokens_hash_key',
  'status token hashes are database-unique'
);

select * from finish();
rollback;
