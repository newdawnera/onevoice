-- Rollback-only Phase 2E idempotency, provenance, review, and reminder test.
begin;

insert into auth.users (id, email, raw_user_meta_data, created_at, updated_at)
values
  ('00000000-0000-4000-8000-00000000005a', 'phase2e-a@example.invalid', '{"display_name":"Phase 2E A"}'::jsonb, now(), now()),
  ('00000000-0000-4000-8000-00000000005b', 'phase2e-b@example.invalid', '{"display_name":"Phase 2E B"}'::jsonb, now(), now());

insert into public.action_items (
  id, user_id, title, assignee, assignee_email, status, source, deadline,
  review_status, reviewed_at
) values
  (
    '20000000-0000-4000-8000-00000000005a',
    '00000000-0000-4000-8000-00000000005a',
    'Direct AI proposal', 'Alice', 'alice@example.invalid', 'not_started',
    'ai_generated', current_date, 'confirmed', now()
  ),
  (
    '20000000-0000-4000-8000-00000000005b',
    '00000000-0000-4000-8000-00000000005a',
    'Manual action', 'Alice', 'alice@example.invalid', 'not_started',
    'manual', current_date, 'pending', null
  );

do $test$
declare
  claim jsonb;
  replay jsonb;
  persisted jsonb;
  reviewed jsonb;
  reminder jsonb;
  scheduled jsonb;
  prepared jsonb;
  generated_action_id uuid;
  delivery_id uuid;
  delivery_claim uuid;
begin
  if (select review_status from public.action_items where id = '20000000-0000-4000-8000-00000000005a') <> 'pending'
    or (select reviewed_at from public.action_items where id = '20000000-0000-4000-8000-00000000005a') is not null then
    raise exception 'AI insert did not fail closed to pending review';
  end if;
  if (select review_status from public.action_items where id = '20000000-0000-4000-8000-00000000005b') <> 'confirmed'
    or (select reviewed_at from public.action_items where id = '20000000-0000-4000-8000-00000000005b') is null then
    raise exception 'Manual insert did not begin confirmed';
  end if;

  reminder := public.claim_manual_reminder(
    '00000000-0000-4000-8000-00000000005a',
    '20000000-0000-4000-8000-00000000005a',
    '30000000-0000-4000-8000-000000000050',
    '40000000-0000-4000-8000-000000000050', 180, 4
  );
  if reminder ->> 'outcome' <> 'ineligible' then
    raise exception 'Pending AI action entered the manual reminder path';
  end if;
  scheduled := public.claim_scheduled_reminders(current_date, 20, 180, 4);
  if exists (
    select 1 from pg_catalog.jsonb_array_elements(scheduled) as item(value)
    join public.reminder_deliveries delivery
      on delivery.id = (item.value ->> 'delivery_id')::uuid
    where delivery.action_item_id = '20000000-0000-4000-8000-00000000005a'
  ) then
    raise exception 'Pending AI action entered the scheduler';
  end if;

  claim := public.claim_ai_generation(
    '00000000-0000-4000-8000-00000000005a', 'meeting_generation',
    '30000000-0000-4000-8000-00000000005a',
    repeat('a', 64), '40000000-0000-4000-8000-00000000005a', 300
  );
  if claim ->> 'outcome' <> 'claimed' then
    raise exception 'Initial generation was not claimed';
  end if;
  if not public.mark_ai_generation_provider_started(
    '00000000-0000-4000-8000-00000000005a',
    '30000000-0000-4000-8000-00000000005a',
    '40000000-0000-4000-8000-00000000005a'
  ) then
    raise exception 'Provider start marker was not written';
  end if;

  persisted := public.persist_ai_generation(
    '00000000-0000-4000-8000-00000000005a',
    '30000000-0000-4000-8000-00000000005a',
    '40000000-0000-4000-8000-00000000005a',
    'text', null, 'Alice will ship the plan. Email alice@example.invalid.',
    '<p>Safe local HTML</p>', 'Safe plain summary', 'Project update',
    'Engineer', 'English', 'groq', 'openai/gpt-oss-20b',
    'phase2e-v1', 'stop',
    '[{"title":"Ship the plan","assignee":"Alice","assignee_email":"alice@example.invalid","start_date":null,"deadline":null,"evidence":"Alice will ship the plan"}]'::jsonb,
    100, 30, 1
  );
  generated_action_id := (persisted -> 'actions' -> 0 ->> 'id')::uuid;
  if persisted ->> 'ai_provider' <> 'groq'
    or persisted ->> 'ai_model' <> 'openai/gpt-oss-20b'
    or persisted ->> 'prompt_version' <> 'phase2e-v1'
    or (select review_status from public.action_items where id = generated_action_id) <> 'pending'
    or (select reviewed_at from public.action_items where id = generated_action_id) is not null then
    raise exception 'Server provenance or pending-review persistence is incorrect';
  end if;

  replay := public.claim_ai_generation(
    '00000000-0000-4000-8000-00000000005a', 'meeting_generation',
    '30000000-0000-4000-8000-00000000005a',
    repeat('a', 64), '40000000-0000-4000-8000-00000000005b', 300
  );
  if replay ->> 'outcome' <> 'completed'
    or public.get_ai_generation_result(
      '00000000-0000-4000-8000-00000000005a',
      '30000000-0000-4000-8000-00000000005a'
    ) ->> 'meeting_id' <> persisted ->> 'meeting_id' then
    raise exception 'Completed generation replay was not reconstructed';
  end if;
  replay := public.claim_ai_generation(
    '00000000-0000-4000-8000-00000000005a', 'meeting_generation',
    '30000000-0000-4000-8000-00000000005a',
    repeat('b', 64), '40000000-0000-4000-8000-00000000005c', 300
  );
  if replay ->> 'outcome' <> 'conflict' then
    raise exception 'Same generation key accepted a different request';
  end if;
  if public.get_ai_generation_result(
    '00000000-0000-4000-8000-00000000005b',
    '30000000-0000-4000-8000-00000000005a'
  ) is not null then
    raise exception 'Cross-user generation result was disclosed';
  end if;
  claim := public.claim_ai_generation(
    '00000000-0000-4000-8000-00000000005b', 'meeting_generation',
    '30000000-0000-4000-8000-00000000005a',
    repeat('c', 64), '40000000-0000-4000-8000-00000000005d', 300
  );
  if claim ->> 'outcome' <> 'claimed' then
    raise exception 'Generation keys collided across users';
  end if;

  claim := public.claim_ai_generation(
    '00000000-0000-4000-8000-00000000005a', 'meeting_generation',
    '30000000-0000-4000-8000-00000000005e',
    repeat('d', 64), '40000000-0000-4000-8000-00000000005e', 60
  );
  update private.ai_generation_requests
  set lease_expires_at = now() - interval '1 second'
  where user_id = '00000000-0000-4000-8000-00000000005a'
    and idempotency_key = '30000000-0000-4000-8000-00000000005e';
  replay := public.claim_ai_generation(
    '00000000-0000-4000-8000-00000000005a', 'meeting_generation',
    '30000000-0000-4000-8000-00000000005e',
    repeat('d', 64), '40000000-0000-4000-8000-00000000005f', 60
  );
  if replay ->> 'outcome' <> 'claimed' then
    raise exception 'Safe abandoned generation claim was not recovered';
  end if;

  reviewed := public.review_ai_action(
    '00000000-0000-4000-8000-00000000005b', generated_action_id,
    'confirmed', 'Ship the plan', 'Alice', 'alice@example.invalid', null, null
  );
  if reviewed ->> 'outcome' <> 'not_found' then
    raise exception 'Cross-user action review disclosed existence';
  end if;
  reviewed := public.review_ai_action(
    '00000000-0000-4000-8000-00000000005a', generated_action_id,
    'confirmed', 'Ship the plan', 'Alice', 'alice@example.invalid', null, current_date
  );
  if reviewed ->> 'outcome' <> 'reviewed'
    or reviewed -> 'action' ->> 'review_status' <> 'confirmed' then
    raise exception 'Owner could not atomically confirm the proposal';
  end if;

  reminder := public.claim_manual_reminder(
    '00000000-0000-4000-8000-00000000005a', generated_action_id,
    '30000000-0000-4000-8000-00000000005f',
    '40000000-0000-4000-8000-000000000060', 180, 4
  );
  if reminder ->> 'outcome' <> 'claimed' then
    raise exception 'Confirmed AI action was not reminder eligible';
  end if;
  delivery_id := (reminder ->> 'delivery_id')::uuid;
  delivery_claim := (reminder ->> 'claim_token')::uuid;

  update public.action_items set title = 'Edited after approval' where id = generated_action_id;
  if (select review_status from public.action_items where id = generated_action_id) <> 'pending'
    or (select status from public.reminder_deliveries where id = delivery_id) <> 'cancelled' then
    raise exception 'Material edit did not reset review and cancel unsent work';
  end if;

  insert into public.reminder_deliveries (
    action_item_id, request_owner_id, manual_idempotency_key, reminder_type,
    dedupe_key, status, attempt_count, claimed_at, claim_token, lease_expires_at
  ) values (
    generated_action_id, '00000000-0000-4000-8000-00000000005a',
    '30000000-0000-4000-8000-000000000060', 'manual_notification',
    'phase2e-prepare-pending', 'processing', 1, now(),
    '40000000-0000-4000-8000-000000000061', now() + interval '3 minutes'
  ) returning id into delivery_id;
  prepared := public.prepare_reminder_delivery(
    delivery_id, '40000000-0000-4000-8000-000000000061',
    '50000000-0000-4000-8000-000000000061', now() + interval '1 hour',
    '[{"target_status":"in_progress","token_hash":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},{"target_status":"completed","token_hash":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}]'::jsonb
  );
  if prepared ->> 'outcome' <> 'cancelled'
    or public.mark_reminder_send_started(
      delivery_id, '40000000-0000-4000-8000-000000000061'
    ) then
    raise exception 'Preparation/final pre-send revalidation accepted an unconfirmed AI action';
  end if;

  insert into public.reminder_deliveries (
    action_item_id, request_owner_id, manual_idempotency_key, reminder_type,
    dedupe_key, status, attempt_count, next_attempt_at
  ) values (
    generated_action_id, '00000000-0000-4000-8000-00000000005a',
    '30000000-0000-4000-8000-000000000062', 'manual_notification',
    'phase2e-retry-pending', 'failed', 1, now() - interval '1 second'
  ) returning id into delivery_id;
  scheduled := public.claim_scheduled_reminders(current_date, 20, 180, 4);
  if exists (
    select 1 from pg_catalog.jsonb_array_elements(scheduled) as item(value)
    where item.value ->> 'delivery_id' = delivery_id::text
  ) then
    raise exception 'Retry worker reclaimed an unconfirmed AI action';
  end if;

  insert into public.reminder_deliveries (
    action_item_id, request_owner_id, manual_idempotency_key, reminder_type,
    dedupe_key, status, attempt_count, sent_at
  ) values (
    generated_action_id, '00000000-0000-4000-8000-00000000005a',
    '30000000-0000-4000-8000-000000000063', 'manual_notification',
    'phase2e-sent-history', 'sent', 1, now()
  );
  insert into public.reminder_deliveries (
    action_item_id, request_owner_id, manual_idempotency_key, reminder_type,
    dedupe_key, status, attempt_count, failure_code
  ) values (
    generated_action_id, '00000000-0000-4000-8000-00000000005a',
    '30000000-0000-4000-8000-000000000064', 'manual_notification',
    'phase2e-unknown-history', 'unknown', 1, 'ambiguous_provider_result'
  );
  reviewed := public.review_ai_action(
    '00000000-0000-4000-8000-00000000005a', generated_action_id,
    'rejected', 'Edited after approval', 'Alice', 'alice@example.invalid', null, current_date
  );
  if (select count(*) from public.reminder_deliveries
      where action_item_id = generated_action_id and status in ('sent', 'unknown')) <> 2 then
    raise exception 'Sent or unknown delivery history was rewritten';
  end if;
end;
$test$;

rollback;
