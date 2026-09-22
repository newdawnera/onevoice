-- Rollback-only smoke test for the linked Phase 2A schema.
-- Run with: npx supabase db query --linked --file supabase/tests/database/phase2a_security_smoke.sql

begin;

insert into auth.users (id, email, raw_user_meta_data, created_at, updated_at)
values
  (
    '00000000-0000-4000-8000-00000000000a',
    'phase2a-a@example.invalid',
    '{"display_name":"Phase 2A User A","username":"Ignored Username"}'::jsonb,
    now(),
    now()
  ),
  (
    '00000000-0000-4000-8000-00000000000b',
    'phase2a-b@example.invalid',
    '{"username":"Phase 2A User B"}'::jsonb,
    now(),
    now()
  ),
  (
    '00000000-0000-4000-8000-00000000000c',
    'phase2a-c@example.invalid',
    '{}'::jsonb,
    now(),
    now()
  ),
  (
    '00000000-0000-4000-8000-00000000000d',
    null,
    '{}'::jsonb,
    now(),
    now()
  );

do $test$
begin
  if (
    select count(*)
    from public.profiles
    where id in (
      '00000000-0000-4000-8000-00000000000a',
      '00000000-0000-4000-8000-00000000000b',
      '00000000-0000-4000-8000-00000000000c',
      '00000000-0000-4000-8000-00000000000d'
    )
  ) <> 4 then
    raise exception 'Auth trigger did not create all profiles';
  end if;

  if (
    select display_name
    from public.profiles
    where id = '00000000-0000-4000-8000-00000000000a'
  ) <> 'Phase 2A User A' then
    raise exception 'display_name metadata did not take precedence';
  end if;

  if (
    select display_name
    from public.profiles
    where id = '00000000-0000-4000-8000-00000000000b'
  ) <> 'Phase 2A User B' then
    raise exception 'username metadata fallback did not apply';
  end if;

  if (
    select display_name
    from public.profiles
    where id = '00000000-0000-4000-8000-00000000000c'
  ) <> 'phase2a-c' then
    raise exception 'email local-part fallback did not apply';
  end if;

  if (
    select display_name
    from public.profiles
    where id = '00000000-0000-4000-8000-00000000000d'
  ) <> 'User' then
    raise exception 'neutral display-name fallback did not apply';
  end if;
end;
$test$;

insert into public.meeting_records (id, user_id, source_text, summary_text)
values
  (
    '10000000-0000-4000-8000-00000000000a',
    '00000000-0000-4000-8000-00000000000a',
    'User A rollback-only meeting',
    'User A rollback-only summary'
  ),
  (
    '10000000-0000-4000-8000-00000000000b',
    '00000000-0000-4000-8000-00000000000b',
    'User B rollback-only meeting',
    'User B rollback-only summary'
  );

insert into public.action_items (id, user_id, meeting_id, title)
values
  (
    '20000000-0000-4000-8000-00000000000b',
    '00000000-0000-4000-8000-00000000000b',
    '10000000-0000-4000-8000-00000000000b',
    'User B rollback-only action'
  );

-- The composite foreign key must reject a cross-owner meeting link even when
-- RLS is bypassed by the migration/test role.
do $test$
begin
  begin
    insert into public.action_items (user_id, meeting_id, title)
    values (
      '00000000-0000-4000-8000-00000000000a',
      '10000000-0000-4000-8000-00000000000b',
      'Must fail at the ownership foreign key'
    );
    raise exception 'Cross-owner meeting link unexpectedly succeeded';
  exception
    when foreign_key_violation then null;
  end;
end;
$test$;

-- Multiple manual reminders on the same date remain valid when no dedupe key
-- is supplied.
insert into public.action_items (id, user_id, meeting_id, title)
values (
  '20000000-0000-4000-8000-00000000000a',
  '00000000-0000-4000-8000-00000000000a',
  '10000000-0000-4000-8000-00000000000a',
  'User A rollback-only action'
);

insert into public.reminder_deliveries (
  action_item_id,
  reminder_type,
  scheduled_for
)
values
  (
    '20000000-0000-4000-8000-00000000000a',
    'manual_notification',
    current_date
  ),
  (
    '20000000-0000-4000-8000-00000000000a',
    'manual_notification',
    current_date
  );

set local role authenticated;
select set_config(
  'request.jwt.claims',
  '{"sub":"00000000-0000-4000-8000-00000000000a","role":"authenticated"}',
  true
);

do $test$
declare
  changed_rows integer;
begin
  if (select auth.uid()) <> '00000000-0000-4000-8000-00000000000a'::uuid then
    raise exception 'Authenticated test JWT was not applied';
  end if;

  if (select count(*) from public.meeting_records) <> 1 then
    raise exception 'User A can see another user''s meeting';
  end if;

  if (select count(*) from public.action_items) <> 1 then
    raise exception 'User A can see another user''s action';
  end if;

  begin
    update public.meeting_records
    set summary_text = 'Forbidden cross-user update'
    where id = '10000000-0000-4000-8000-00000000000b';
    get diagnostics changed_rows = row_count;
    if changed_rows <> 0 then
      raise exception 'User A updated User B''s meeting';
    end if;
  exception
    -- Phase 2C removes the browser meeting UPDATE grant entirely.
    when insufficient_privilege then null;
  end;

  delete from public.action_items
  where id = '20000000-0000-4000-8000-00000000000b';
  get diagnostics changed_rows = row_count;
  if changed_rows <> 0 then
    raise exception 'User A deleted User B''s action';
  end if;

  begin
    insert into public.action_items (user_id, meeting_id, title)
    values (
      '00000000-0000-4000-8000-00000000000a',
      '10000000-0000-4000-8000-00000000000b',
      'Must fail at RLS'
    );
    raise exception 'RLS allowed a cross-owner meeting link';
  exception
    when insufficient_privilege or foreign_key_violation then null;
  end;
end;
$test$;

reset role;
set local role anon;
select set_config('request.jwt.claims', '{}', true);

do $test$
begin
  begin
    perform 1 from public.meeting_records limit 1;
    raise exception 'Anonymous SELECT unexpectedly succeeded';
  exception
    when insufficient_privilege then null;
  end;

  begin
    insert into public.meeting_records (user_id, source_text)
    values (
      '00000000-0000-4000-8000-00000000000a',
      'Anonymous insert must fail'
    );
    raise exception 'Anonymous INSERT unexpectedly succeeded';
  exception
    when insufficient_privilege then null;
  end;
end;
$test$;

reset role;
rollback;
