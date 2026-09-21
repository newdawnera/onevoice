begin;

create extension if not exists pgtap with schema extensions;
alter extension pgtap set schema extensions;
set local role postgres;
set local search_path = public, extensions, pg_catalog;

select plan(14);

select has_table('public', 'profiles', 'profiles exists');
select has_table('public', 'meeting_records', 'meeting_records exists');
select has_table('public', 'action_items', 'action_items exists');
select has_table('public', 'reminder_deliveries', 'reminder_deliveries exists');
select has_column(
  'public',
  'meeting_records',
  'client_request_id',
  'meeting idempotency column exists'
);
select has_function(
  'public',
  'save_meeting_with_actions',
  array[
    'uuid', 'text', 'text', 'text', 'text', 'text', 'text',
    'text', 'text', 'text', 'text', 'text', 'jsonb'
  ],
  'atomic meeting RPC exists'
);
select ok(
  (select relrowsecurity from pg_class where oid = 'public.profiles'::regclass),
  'profiles has RLS enabled'
);
select ok(
  (select relrowsecurity from pg_class where oid = 'public.meeting_records'::regclass),
  'meeting_records has RLS enabled'
);
select ok(
  (select relrowsecurity from pg_class where oid = 'public.action_items'::regclass),
  'action_items has RLS enabled'
);
select ok(
  (select relrowsecurity from pg_class where oid = 'public.reminder_deliveries'::regclass),
  'reminder_deliveries has RLS enabled'
);
select ok(
  not has_table_privilege('anon', 'public.meeting_records', 'select'),
  'anon has no meeting read grant'
);
select ok(
  not has_table_privilege('authenticated', 'public.meeting_records', 'insert'),
  'authenticated has no direct meeting insert grant'
);
select ok(
  not has_function_privilege(
    'anon',
    'public.save_meeting_with_actions(uuid,text,text,text,text,text,text,text,text,text,text,text,jsonb)',
    'execute'
  ),
  'anon cannot execute the atomic meeting RPC'
);
select results_eq(
  $$
    select tablename::text collate "C"
    from pg_publication_tables
    where pubname = 'supabase_realtime'
      and schemaname = 'public'
      and tablename in ('meeting_records', 'action_items')
    order by tablename
  $$,
  $$
    values
      ('action_items'::text collate "C"),
      ('meeting_records'::text collate "C")
  $$,
  'the application data tables are published for Realtime'
);

select * from finish();
rollback;
