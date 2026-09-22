-- Rollback-only Phase 2C security and atomicity test.
-- Run with:
-- npx supabase db query --linked --file supabase/tests/database/phase2c_security_smoke.sql

begin;

insert into auth.users (id, email, raw_user_meta_data, created_at, updated_at)
values
  (
    '00000000-0000-4000-8000-00000000002a',
    'phase2c-a@example.invalid',
    '{"display_name":"Phase 2C User A"}'::jsonb,
    now(),
    now()
  ),
  (
    '00000000-0000-4000-8000-00000000002b',
    'phase2c-b@example.invalid',
    '{"display_name":"Phase 2C User B"}'::jsonb,
    now(),
    now()
  );

insert into public.meeting_records (
  id,
  user_id,
  client_request_id,
  source_type,
  source_text,
  summary_text
)
values
  (
    '10000000-0000-4000-8000-00000000002a',
    '00000000-0000-4000-8000-00000000002a',
    '30000000-0000-4000-8000-00000000002a',
    'text',
    'User A seed source',
    'User A seed summary'
  ),
  (
    '10000000-0000-4000-8000-00000000002b',
    '00000000-0000-4000-8000-00000000002b',
    '30000000-0000-4000-8000-00000000002b',
    'text',
    'User B seed source',
    'User B seed summary'
  );

insert into public.action_items (
  id,
  user_id,
  meeting_id,
  title,
  source
)
values (
  '20000000-0000-4000-8000-00000000002b',
  '00000000-0000-4000-8000-00000000002b',
  '10000000-0000-4000-8000-00000000002b',
  'User B seed action',
  'ai_generated'
);

do $test$
begin
  if pg_catalog.has_table_privilege('anon', 'public.profiles', 'select')
    or pg_catalog.has_table_privilege('anon', 'public.meeting_records', 'select')
    or pg_catalog.has_table_privilege('anon', 'public.action_items', 'select')
    or pg_catalog.has_table_privilege('anon', 'public.reminder_deliveries', 'select') then
    raise exception 'anon unexpectedly has application-table access';
  end if;

  if not pg_catalog.has_table_privilege(
    'authenticated', 'public.meeting_records', 'select'
  ) then
    raise exception 'authenticated is missing the meeting SELECT grant';
  end if;
  if pg_catalog.has_table_privilege(
    'authenticated', 'public.meeting_records', 'insert'
  ) or pg_catalog.has_table_privilege(
    'authenticated', 'public.meeting_records', 'update'
  ) or pg_catalog.has_table_privilege(
    'authenticated', 'public.meeting_records', 'delete'
  ) then
    raise exception 'authenticated has a direct meeting write grant';
  end if;

  if not pg_catalog.has_column_privilege(
    'authenticated', 'public.profiles', 'display_name', 'update'
  ) or pg_catalog.has_column_privilege(
    'authenticated', 'public.profiles', 'id', 'update'
  ) then
    raise exception 'profile column grants are incorrect';
  end if;

  if pg_catalog.has_column_privilege(
    'authenticated', 'public.action_items', 'source', 'update'
  ) or pg_catalog.has_column_privilege(
    'authenticated', 'public.action_items', 'meeting_id', 'update'
  ) or pg_catalog.has_column_privilege(
    'authenticated', 'public.action_items', 'user_id', 'update'
  ) then
    raise exception 'immutable action columns are updateable';
  end if;

  if pg_catalog.has_table_privilege(
    'authenticated', 'public.reminder_deliveries', 'select'
  ) or pg_catalog.has_table_privilege(
    'authenticated', 'public.reminder_deliveries', 'insert'
  ) then
    raise exception 'authenticated can access reminder deliveries';
  end if;

  if pg_catalog.has_function_privilege(
    'anon',
    'public.save_meeting_with_actions(uuid,text,text,text,text,text,text,text,text,text,text,text,jsonb)',
    'execute'
  ) then
    raise exception 'anon can execute the atomic meeting function';
  end if;
  if not pg_catalog.has_function_privilege(
    'authenticated',
    'public.save_meeting_with_actions(uuid,text,text,text,text,text,text,text,text,text,text,text,jsonb)',
    'execute'
  ) then
    raise exception 'authenticated cannot execute the atomic meeting function';
  end if;

  if exists (
    select 1
    from pg_catalog.pg_proc as proc
    join pg_catalog.pg_namespace as namespace on namespace.oid = proc.pronamespace
    where namespace.nspname = 'public'
      and proc.proname = 'save_meeting_with_actions'
      and 'user_id' = any(coalesce(proc.proargnames, array[]::text[]))
  ) then
    raise exception 'The atomic meeting function accepts a user_id parameter';
  end if;

  if not exists (
    select 1 from pg_catalog.pg_publication_tables
    where pubname = 'supabase_realtime'
      and schemaname = 'public'
      and tablename = 'meeting_records'
  ) or not exists (
    select 1 from pg_catalog.pg_publication_tables
    where pubname = 'supabase_realtime'
      and schemaname = 'public'
      and tablename = 'action_items'
  ) then
    raise exception 'Required application tables are absent from Realtime';
  end if;

  if exists (
    select 1 from pg_catalog.pg_publication_tables
    where pubname = 'supabase_realtime'
      and schemaname = 'public'
      and tablename = 'reminder_deliveries'
  ) then
    raise exception 'Reminder deliveries were unnecessarily added to Realtime';
  end if;
end;
$test$;

set local role anon;
select set_config('request.jwt.claims', '{}', true);

do $test$
begin
  begin
    perform 1 from public.profiles limit 1;
    raise exception 'Anonymous profile SELECT unexpectedly succeeded';
  exception when insufficient_privilege then null;
  end;

  begin
    perform public.save_meeting_with_actions(
      '30000000-0000-4000-8000-000000000020',
      'text', null, null, 'Anonymous source', null, 'Anonymous summary',
      null, null, null, null, null, '[]'::jsonb
    );
    raise exception 'Anonymous atomic save unexpectedly succeeded';
  exception when insufficient_privilege then null;
  end;
end;
$test$;

reset role;
set local role authenticated;
select set_config(
  'request.jwt.claims',
  '{"sub":"00000000-0000-4000-8000-00000000002a","role":"authenticated"}',
  true
);

do $test$
declare
  v_changed integer;
  v_manual_id uuid;
  v_meeting_id uuid;
  v_retry_id uuid;
  v_actions jsonb;
begin
  if (select count(*) from public.profiles) <> 1 then
    raise exception 'User A can read another profile';
  end if;
  if (select count(*) from public.meeting_records) <> 1 then
    raise exception 'User A can read another meeting';
  end if;
  if (select count(*) from public.action_items) <> 0 then
    raise exception 'User A can read another action';
  end if;

  update public.profiles
  set display_name = 'Updated User A'
  where id = '00000000-0000-4000-8000-00000000002a';
  get diagnostics v_changed = row_count;
  if v_changed <> 1 then
    raise exception 'User A could not update their display name';
  end if;

  update public.profiles
  set display_name = 'Forbidden'
  where id = '00000000-0000-4000-8000-00000000002b';
  get diagnostics v_changed = row_count;
  if v_changed <> 0 then
    raise exception 'User A updated User B profile';
  end if;

  begin
    update public.profiles
    set id = '00000000-0000-4000-8000-00000000002b'
    where id = '00000000-0000-4000-8000-00000000002a';
    raise exception 'Profile ownership update unexpectedly succeeded';
  exception when insufficient_privilege then null;
  end;

  begin
    insert into public.meeting_records (
      user_id, client_request_id, source_type, source_text, summary_text
    ) values (
      '00000000-0000-4000-8000-00000000002a',
      '30000000-0000-4000-8000-000000000021',
      'text',
      'Partial source',
      'Partial summary'
    );
    raise exception 'Direct meeting insert unexpectedly succeeded';
  exception when insufficient_privilege then null;
  end;

  v_meeting_id := public.save_meeting_with_actions(
    '30000000-0000-4000-8000-000000000022',
    'document',
    'notes.txt',
    '<p>Source</p>',
    'Source',
    '<p>Summary</p>',
    'Summary',
    'Subject',
    'Manager',
    'No Translation',
    'google_gemini',
    'gemini-2.5-flash',
    '[{"task":"Ship the release","assignee":"Alex","assigneeEmail":"alex@example.com","startDate":"2026-09-20","deadline":"2026-09-21"}]'::jsonb
  );

  select meeting.extracted_actions_snapshot
  into v_actions
  from public.meeting_records as meeting
  where meeting.id = v_meeting_id;
  if v_actions <> '[{"title":"Ship the release","assignee":"Alex","assigneeEmail":"alex@example.com","startDate":"2026-09-20","deadline":"2026-09-21"}]'::jsonb then
    raise exception 'The normalized action snapshot is incorrect';
  end if;
  if (
    select count(*) from public.action_items
    where meeting_id = v_meeting_id
      and user_id = '00000000-0000-4000-8000-00000000002a'
      and source = 'ai_generated'
      and status = 'not_started'
  ) <> 1 then
    raise exception 'Atomic AI action ownership/provenance is incorrect';
  end if;

  v_retry_id := public.save_meeting_with_actions(
    '30000000-0000-4000-8000-000000000022',
    'text', null, null, 'Different retry source', null, 'Different retry summary',
    null, null, null, null, null, '[]'::jsonb
  );
  if v_retry_id <> v_meeting_id then
    raise exception 'An idempotent retry returned a different meeting';
  end if;
  if (select count(*) from public.action_items where meeting_id = v_meeting_id) <> 1 then
    raise exception 'An idempotent retry duplicated actions';
  end if;

  begin
    perform public.save_meeting_with_actions(
      '30000000-0000-4000-8000-000000000023',
      'text', null, null, 'Source', null, 'Summary', null, null, null, null, null,
      '{"title":"not an array"}'::jsonb
    );
    raise exception 'A non-array action payload unexpectedly succeeded';
  exception when invalid_parameter_value then null;
  end;

  begin
    perform public.save_meeting_with_actions(
      '30000000-0000-4000-8000-000000000024',
      'text', null, null, 'Source', null, 'Summary', null, null, null, null, null,
      '[{"title":"Action","prompt":"must not persist"}]'::jsonb
    );
    raise exception 'An unknown action field unexpectedly succeeded';
  exception when invalid_parameter_value then null;
  end;

  begin
    perform public.save_meeting_with_actions(
      '30000000-0000-4000-8000-000000000025',
      'text', null, null, 'Source', null, 'Summary', null, null, null, null, null,
      '[{"title":"Valid"},{"title":"Invalid date order","startDate":"2026-09-22","deadline":"2026-09-21"}]'::jsonb
    );
    raise exception 'An invalid date order unexpectedly succeeded';
  exception when invalid_parameter_value then null;
  end;
  if exists (
    select 1 from public.meeting_records
    where client_request_id = '30000000-0000-4000-8000-000000000025'
  ) then
    raise exception 'A failed action did not roll back the meeting';
  end if;

  begin
    perform public.save_meeting_with_actions(
      '30000000-0000-4000-8000-000000000026',
      'text', null, null, 'Source', null, 'Summary', null, null, null, null, null,
      (
        select jsonb_agg(jsonb_build_object('title', 'Action ' || value))
        from generate_series(1, 101) as value
      )
    );
    raise exception 'An excessive action count unexpectedly succeeded';
  exception when invalid_parameter_value then null;
  end;

  insert into public.action_items (
    user_id, title, assignee, assignee_email, start_date, deadline
  ) values (
    '00000000-0000-4000-8000-00000000002a',
    'Manual action',
    'User A',
    'user-a@example.com',
    '2026-09-20',
    '2026-09-21'
  ) returning id into v_manual_id;

  if not exists (
    select 1 from public.action_items
    where id = v_manual_id
      and source = 'manual'
      and status = 'not_started'
      and meeting_id is null
  ) then
    raise exception 'Manual action defaults are incorrect';
  end if;

  begin
    insert into public.action_items (user_id, title, source)
    values (
      '00000000-0000-4000-8000-00000000002a',
      'Forged AI action',
      'ai_generated'
    );
    raise exception 'A browser insert forged AI provenance';
  exception when insufficient_privilege then null;
  end;

  begin
    insert into public.action_items (user_id, title)
    values (
      '00000000-0000-4000-8000-00000000002b',
      'Cross-user action'
    );
    raise exception 'A cross-user action insert unexpectedly succeeded';
  exception when insufficient_privilege then null;
  end;

  update public.action_items
  set title = 'Forbidden cross-user update'
  where id = '20000000-0000-4000-8000-00000000002b';
  get diagnostics v_changed = row_count;
  if v_changed <> 0 then
    raise exception 'User A updated User B action';
  end if;

  begin
    update public.action_items
    set user_id = '00000000-0000-4000-8000-00000000002b'
    where id = v_manual_id;
    raise exception 'Action ownership update unexpectedly succeeded';
  exception when insufficient_privilege then null;
  end;

  begin
    update public.action_items
    set meeting_id = '10000000-0000-4000-8000-00000000002b'
    where id = v_manual_id;
    raise exception 'Cross-owner meeting link unexpectedly succeeded';
  exception when insufficient_privilege then null;
  end;

  begin
    update public.action_items set status = 'invalid' where id = v_manual_id;
    raise exception 'An invalid status unexpectedly succeeded';
  exception when check_violation then null;
  end;

  begin
    update public.action_items
    set start_date = '2026-09-22', deadline = '2026-09-21'
    where id = v_manual_id;
    raise exception 'An invalid manual date order unexpectedly succeeded';
  exception when check_violation then null;
  end;

  update public.action_items
  set title = 'Updated manual action', status = 'in_progress'
  where id = v_manual_id;
  get diagnostics v_changed = row_count;
  if v_changed <> 1 then
    raise exception 'Allowed manual action update failed';
  end if;

  delete from public.action_items where id = v_manual_id;
  get diagnostics v_changed = row_count;
  if v_changed <> 1 then
    raise exception 'User A could not delete their action';
  end if;
end;
$test$;

reset role;
set local role authenticated;
select set_config(
  'request.jwt.claims',
  '{"sub":"00000000-0000-4000-8000-00000000002b","role":"authenticated"}',
  true
);

select public.save_meeting_with_actions(
  '30000000-0000-4000-8000-000000000022',
  'text', null, null, 'User B source', null, 'User B summary',
  null, null, null, null, null, '[]'::jsonb
);

reset role;

do $test$
begin
  if (
    select count(*) from public.meeting_records
    where client_request_id = '30000000-0000-4000-8000-000000000022'
  ) <> 2 then
    raise exception 'Two users could not independently reuse one request ID';
  end if;

  if (
    select count(distinct id) from public.meeting_records
    where client_request_id = '30000000-0000-4000-8000-000000000022'
  ) <> 2 then
    raise exception 'Cross-user idempotency leaked a meeting ID';
  end if;
end;
$test$;

rollback;
