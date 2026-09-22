-- Rollback-only Phase 2D state-machine, idempotency, and token test.
begin;

insert into auth.users (id, email, raw_user_meta_data, created_at, updated_at)
values
  ('00000000-0000-4000-8000-00000000004a', 'phase2d-a@example.invalid', '{"display_name":"Phase 2D A"}'::jsonb, now(), now()),
  ('00000000-0000-4000-8000-00000000004b', 'phase2d-b@example.invalid', '{"display_name":"Phase 2D B"}'::jsonb, now(), now());

insert into public.action_items (
  id, user_id, title, assignee, assignee_email, status, source, start_date, deadline
)
values
  (
    '20000000-0000-4000-8000-00000000004a',
    '00000000-0000-4000-8000-00000000004a',
    'User A due action', 'Alice', 'alice@example.invalid', 'not_started', 'manual',
    current_date, current_date
  ),
  (
    '20000000-0000-4000-8000-00000000004b',
    '00000000-0000-4000-8000-00000000004b',
    'User B action', 'Bob', 'bob@example.invalid', 'not_started', 'manual',
    current_date, current_date + 1
  ),
  (
    '20000000-0000-4000-8000-00000000004c',
    '00000000-0000-4000-8000-00000000004a',
    'User A second action', 'Alice', 'alice@example.invalid', 'not_started', 'manual',
    null, null
  );

do $test$
declare
  first_claim jsonb;
  duplicate_claim jsonb;
  incompatible_claim jsonb;
  cross_user_claim jsonb;
  prepared jsonb;
  inspected jsonb;
  consumed jsonb;
  replayed jsonb;
  scheduled_claims jsonb;
  duplicate_schedule jsonb;
  manual_retry_claims jsonb;
  delivery_id uuid;
  claim_token uuid;
  scheduled_delivery_id uuid;
begin
  if pg_catalog.has_table_privilege('anon', 'public.action_status_tokens', 'select')
    or pg_catalog.has_table_privilege('authenticated', 'public.action_status_tokens', 'select')
    or pg_catalog.has_table_privilege('authenticated', 'public.reminder_deliveries', 'update') then
    raise exception 'Browser roles can access reminder internals';
  end if;

  if pg_catalog.has_function_privilege(
    'authenticated',
    'public.claim_manual_reminder(uuid,uuid,uuid,uuid,integer,integer)',
    'execute'
  ) or pg_catalog.has_function_privilege(
    'anon',
    'public.consume_action_status_token(text,timestamp with time zone)',
    'execute'
  ) then
    raise exception 'Browser roles can execute privileged reminder functions';
  end if;

  first_claim := public.claim_manual_reminder(
    '00000000-0000-4000-8000-00000000004a',
    '20000000-0000-4000-8000-00000000004a',
    '30000000-0000-4000-8000-00000000004a',
    '40000000-0000-4000-8000-00000000004a',
    180,
    4
  );
  if first_claim ->> 'outcome' <> 'claimed' then
    raise exception 'The first manual reminder was not claimed: %', first_claim;
  end if;
  delivery_id := (first_claim ->> 'delivery_id')::uuid;
  claim_token := (first_claim ->> 'claim_token')::uuid;

  duplicate_claim := public.claim_manual_reminder(
    '00000000-0000-4000-8000-00000000004a',
    '20000000-0000-4000-8000-00000000004a',
    '30000000-0000-4000-8000-00000000004a',
    '40000000-0000-4000-8000-00000000004b',
    180,
    4
  );
  if duplicate_claim ->> 'outcome' <> 'existing'
    or duplicate_claim ->> 'delivery_id' <> delivery_id::text then
    raise exception 'A duplicate manual request was not deduplicated';
  end if;

  incompatible_claim := public.claim_manual_reminder(
    '00000000-0000-4000-8000-00000000004a',
    '20000000-0000-4000-8000-00000000004c',
    '30000000-0000-4000-8000-00000000004a',
    '40000000-0000-4000-8000-00000000004c',
    180,
    4
  );
  if incompatible_claim ->> 'outcome' <> 'incompatible' then
    raise exception 'An idempotency key was reused for a different owned action';
  end if;

  cross_user_claim := public.claim_manual_reminder(
    '00000000-0000-4000-8000-00000000004a',
    '20000000-0000-4000-8000-00000000004b',
    '30000000-0000-4000-8000-00000000004b',
    '40000000-0000-4000-8000-00000000004d',
    180,
    4
  );
  if cross_user_claim ->> 'outcome' <> 'not_found' then
    raise exception 'Cross-user lookup disclosed an action';
  end if;

  cross_user_claim := public.claim_manual_reminder(
    '00000000-0000-4000-8000-00000000004a',
    '90000000-0000-4000-8000-000000000099',
    '30000000-0000-4000-8000-00000000004c',
    '40000000-0000-4000-8000-00000000004e',
    180,
    4
  );
  if cross_user_claim ->> 'outcome' <> 'not_found' then
    raise exception 'Missing action lookup differs from cross-user lookup';
  end if;

  prepared := public.prepare_reminder_delivery(
    delivery_id,
    claim_token,
    '50000000-0000-4000-8000-00000000004a',
    now() + interval '1 hour',
    '[
      {"target_status":"in_progress","token_hash":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
      {"target_status":"completed","token_hash":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}
    ]'::jsonb
  );
  if prepared ->> 'outcome' <> 'prepared' then
    raise exception 'The claimed delivery could not be prepared';
  end if;
  if (select count(*) from public.action_status_tokens where reminder_delivery_id = delivery_id) <> 2 then
    raise exception 'The expected hashed status tokens were not created';
  end if;
  if not public.mark_reminder_send_started(delivery_id, claim_token) then
    raise exception 'The provider attempt marker was not stored';
  end if;

  inspected := public.inspect_action_status_token(
    'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
    now()
  );
  if inspected ->> 'valid' <> 'true'
    or exists (
      select 1 from public.action_status_tokens
      where reminder_delivery_id = delivery_id and used_at is not null
    ) then
    raise exception 'GET-style token inspection mutated or rejected a valid token';
  end if;

  consumed := public.consume_action_status_token(
    'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
    now()
  );
  if consumed ->> 'updated' <> 'true'
    or (select status from public.action_items where id = '20000000-0000-4000-8000-00000000004a') <> 'completed'
    or not exists (
      select 1 from public.action_status_tokens
      where token_hash = decode('aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 'hex')
        and revoked_at is not null
    ) then
    raise exception 'Atomic status transition or sibling revocation failed';
  end if;

  replayed := public.consume_action_status_token(
    'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
    now()
  );
  if replayed ->> 'updated' <> 'false' then
    raise exception 'A status token replay was accepted';
  end if;

  if public.finalize_reminder_delivery(
    delivery_id,
    'ffffffff-ffff-4fff-8fff-ffffffffffff',
    'sent',
    'wrong-worker',
    null,
    null,
    null
  ) then
    raise exception 'A different worker finalized the active claim';
  end if;
  if not public.finalize_reminder_delivery(
    delivery_id,
    claim_token,
    'sent',
    'provider-message',
    null,
    null,
    null
  ) then
    raise exception 'The owning worker could not finalize its claim';
  end if;

  insert into public.action_status_tokens (
    action_item_id, target_status, token_hash, sibling_group_id, expires_at
  ) values (
    '20000000-0000-4000-8000-00000000004a',
    'in_progress',
    decode('cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc', 'hex'),
    '50000000-0000-4000-8000-00000000004b',
    now() + interval '1 hour'
  );
  consumed := public.consume_action_status_token(
    'cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc',
    now()
  );
  if consumed ->> 'updated' <> 'false' then
    raise exception 'A completed action regressed through a status token';
  end if;

  insert into public.action_status_tokens (
    action_item_id, target_status, token_hash, sibling_group_id,
    created_at, expires_at, revoked_at
  ) values
  (
    '20000000-0000-4000-8000-00000000004c',
    'in_progress',
    decode('dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd', 'hex'),
    '50000000-0000-4000-8000-00000000004c',
    now() - interval '2 hours',
    now() - interval '1 hour',
    null
  ),
  (
    '20000000-0000-4000-8000-00000000004c',
    'in_progress',
    decode('eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee', 'hex'),
    '50000000-0000-4000-8000-00000000004d',
    now(),
    now() + interval '1 hour',
    now()
  ),
  (
    '20000000-0000-4000-8000-00000000004c',
    'in_progress',
    decode('7777777777777777777777777777777777777777777777777777777777777777', 'hex'),
    '50000000-0000-4000-8000-00000000004e',
    now(),
    now() + interval '1 hour',
    null
  );

  consumed := public.consume_action_status_token(
    'dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd', now()
  );
  if consumed ->> 'updated' <> 'false' then
    raise exception 'An expired status token was accepted';
  end if;
  consumed := public.consume_action_status_token(
    'eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee', now()
  );
  if consumed ->> 'updated' <> 'false' then
    raise exception 'A revoked status token was accepted';
  end if;
  consumed := public.consume_action_status_token(
    'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff', now()
  );
  if consumed ->> 'updated' <> 'false' then
    raise exception 'An unknown status token was accepted';
  end if;
  consumed := public.consume_action_status_token('not-hex', now());
  if consumed ->> 'updated' <> 'false' then
    raise exception 'A malformed status token was accepted';
  end if;

  consumed := public.consume_action_status_token(
    '7777777777777777777777777777777777777777777777777777777777777777', now()
  );
  if consumed ->> 'updated' <> 'true'
    or (select status from public.action_items where id = '20000000-0000-4000-8000-00000000004c') <> 'in_progress'
    or (select status from public.action_items where id = '20000000-0000-4000-8000-00000000004b') <> 'not_started' then
    raise exception 'A status token was not bound to its stored action and transition';
  end if;

  scheduled_claims := public.claim_scheduled_reminders(current_date, 20, 180, 4);
  duplicate_schedule := public.claim_scheduled_reminders(current_date, 20, 180, 4);
  if pg_catalog.jsonb_array_length(scheduled_claims) <> 1
    or pg_catalog.jsonb_array_length(duplicate_schedule) <> 0 then
    raise exception 'Duplicate scheduler calls acquired the same active delivery';
  end if;
  scheduled_delivery_id := (scheduled_claims -> 0 ->> 'delivery_id')::uuid;

  update public.reminder_deliveries
  set lease_expires_at = now() - interval '1 second'
  where id = scheduled_delivery_id;
  scheduled_claims := public.claim_scheduled_reminders(current_date, 20, 180, 4);
  if pg_catalog.jsonb_array_length(scheduled_claims) <> 1
    or (scheduled_claims -> 0 ->> 'delivery_id')::uuid <> scheduled_delivery_id then
    raise exception 'An expired pre-send lease was not recovered';
  end if;

  update public.reminder_deliveries
  set
    lease_expires_at = now() - interval '1 second',
    provider_attempted_at = now()
  where id = scheduled_delivery_id;
  scheduled_claims := public.claim_scheduled_reminders(current_date, 20, 180, 4);
  if pg_catalog.jsonb_array_length(scheduled_claims) <> 0
    or (select status from public.reminder_deliveries where id = scheduled_delivery_id) <> 'unknown' then
    raise exception 'An expired post-send lease was blindly retried';
  end if;

  update public.reminder_deliveries
  set status = 'failed', attempt_count = 4, next_attempt_at = now() - interval '1 second'
  where id = scheduled_delivery_id;
  scheduled_claims := public.claim_scheduled_reminders(current_date, 20, 180, 4);
  if pg_catalog.jsonb_array_length(scheduled_claims) <> 0 then
    raise exception 'A max-attempt delivery was reclaimed';
  end if;

  if (select count(*) from public.reminder_deliveries where dedupe_key like 'scheduled:%') <> 1 then
    raise exception 'Scheduled deterministic deduplication failed';
  end if;

  update public.reminder_deliveries
  set
    status = 'failed',
    sent_at = null,
    provider_attempted_at = null,
    attempt_count = 1,
    next_attempt_at = now() - interval '1 second'
  where id = delivery_id;
  manual_retry_claims := public.claim_scheduled_reminders(current_date, 20, 180, 4);
  if pg_catalog.jsonb_array_length(manual_retry_claims) <> 1
    or (manual_retry_claims -> 0 ->> 'delivery_id')::uuid <> delivery_id
    or (manual_retry_claims -> 0 ->> 'attempt_count')::integer <> 2 then
    raise exception 'The worker did not reclaim a due manual retry';
  end if;

  insert into public.action_items (
    id, user_id, title, assignee, assignee_email, status, source, deadline
  ) values
  (
    '20000000-0000-4000-8000-00000000004d',
    '00000000-0000-4000-8000-00000000004a',
    'Batch candidate one', 'Alice', 'alice@example.invalid',
    'not_started', 'manual', current_date
  ),
  (
    '20000000-0000-4000-8000-00000000004e',
    '00000000-0000-4000-8000-00000000004a',
    'Batch candidate two', 'Alice', 'alice@example.invalid',
    'not_started', 'manual', current_date
  );
  scheduled_claims := public.claim_scheduled_reminders(current_date, 1, 180, 4);
  duplicate_schedule := public.claim_scheduled_reminders(current_date, 1, 180, 4);
  if pg_catalog.jsonb_array_length(scheduled_claims) <> 1
    or pg_catalog.jsonb_array_length(duplicate_schedule) <> 1
    or (scheduled_claims -> 0 ->> 'delivery_id')
      = (duplicate_schedule -> 0 ->> 'delivery_id') then
    raise exception 'Bounded batches did not claim one distinct delivery at a time';
  end if;
end;
$test$;

rollback;
