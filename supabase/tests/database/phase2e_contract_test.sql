begin;

create extension if not exists pgtap with schema extensions;
alter extension pgtap set schema extensions;
set local role postgres;
set local search_path = public, extensions, pg_catalog;

select plan(30);

select has_table('private', 'ai_generation_requests', 'private generation ledger exists');
select has_column('public', 'meeting_records', 'prompt_version', 'meeting prompt version exists');
select has_column('public', 'action_items', 'review_status', 'action review state exists');
select has_column('public', 'action_items', 'reviewed_at', 'action review timestamp exists');
select has_column('public', 'action_items', 'ai_evidence', 'AI evidence exists');
select has_column('private', 'ai_generation_requests', 'request_hash', 'request hash exists');
select has_column('private', 'ai_generation_requests', 'provider_started_at', 'provider start marker exists');
select has_column('private', 'ai_generation_requests', 'meeting_id', 'completed generation links to its meeting');
select ok(
  (select relrowsecurity from pg_class where oid = 'private.ai_generation_requests'::regclass),
  'generation ledger has RLS enabled'
);
select ok(not has_table_privilege('anon', 'private.ai_generation_requests', 'select'), 'anon cannot read generation metadata');
select ok(not has_table_privilege('authenticated', 'private.ai_generation_requests', 'select'), 'authenticated cannot read generation metadata');
select ok(has_table_privilege('service_role', 'private.ai_generation_requests', 'select'), 'service role can read generation metadata');
select ok(
  not has_column_privilege('authenticated', 'public.action_items', 'review_status', 'update'),
  'browser role cannot forge review state'
);
select ok(
  not has_column_privilege('authenticated', 'public.action_items', 'reviewed_at', 'update'),
  'browser role cannot forge review timestamps'
);
select ok(
  has_column_privilege('authenticated', 'public.action_items', 'title', 'update'),
  'browser retains ordinary action editing'
);
select has_trigger('public', 'action_items', 'action_items_set_review_defaults', 'insert provenance sets safe review defaults');
select has_trigger('public', 'action_items', 'action_items_enforce_immutability', 'material edits invalidate approval');
select has_trigger('public', 'action_items', 'action_items_invalidate_unreviewed_reminders', 'review revocation invalidates reminders');
select has_function('public', 'claim_ai_generation', array['uuid','text','uuid','text','uuid','integer'], 'generation claim RPC exists');
select has_function('public', 'mark_ai_generation_provider_started', array['uuid','uuid','uuid'], 'provider marker RPC exists');
select has_function('public', 'persist_ai_generation', array['uuid','uuid','uuid','text','text','text','text','text','text','text','text','text','text','text','text','jsonb','integer','integer','integer'], 'atomic generation persistence RPC exists');
select has_function('public', 'get_ai_generation_result', array['uuid','uuid'], 'generation replay RPC exists');
select has_function('public', 'review_ai_action', array['uuid','uuid','text','text','text','text','date','date'], 'action review RPC exists');
select ok(not has_function_privilege('authenticated', 'public.claim_ai_generation(uuid,text,uuid,text,uuid,integer)', 'execute'), 'browser cannot claim provider work');
select ok(not has_function_privilege('authenticated', 'public.persist_ai_generation(uuid,uuid,uuid,text,text,text,text,text,text,text,text,text,text,text,text,jsonb,integer,integer,integer)', 'execute'), 'browser cannot persist provider provenance');
select ok(not has_function_privilege('authenticated', 'public.review_ai_action(uuid,uuid,text,text,text,text,date,date)', 'execute'), 'browser cannot bypass the authenticated backend review route');
select ok(not has_function_privilege('authenticated', 'public.save_meeting_with_actions(uuid,text,text,text,text,text,text,text,text,text,text,text,jsonb)', 'execute'), 'legacy browser AI save RPC is revoked');
select ok(has_function_privilege('service_role', 'public.persist_ai_generation(uuid,uuid,uuid,text,text,text,text,text,text,text,text,text,text,text,text,jsonb,integer,integer,integer)', 'execute'), 'service role can atomically persist a generation');
select ok(has_function_privilege('service_role', 'public.review_ai_action(uuid,uuid,text,text,text,text,date,date)', 'execute'), 'service role can review for a verified owner');
select index_is_unique('private', 'ai_generation_requests', 'ai_generation_requests_user_operation_key', 'generation keys are unique per user and operation');

select * from finish();
rollback;
