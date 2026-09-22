begin;

create or replace function public.claim_manual_reminder(
  p_user_id uuid,
  p_action_item_id uuid,
  p_idempotency_key uuid,
  p_claim_token uuid,
  p_lease_seconds integer,
  p_max_attempts integer
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $function$
declare
  v_action public.action_items%rowtype;
  v_delivery public.reminder_deliveries%rowtype;
  v_dedupe_key text;
begin
  if p_user_id is null or p_action_item_id is null or p_idempotency_key is null
    or p_claim_token is null or p_lease_seconds not between 60 and 900
    or p_max_attempts not between 1 and 10 then
    raise exception 'Invalid manual reminder claim input.' using errcode = '22023';
  end if;

  select action.* into v_action
  from public.action_items as action
  where action.id = p_action_item_id and action.user_id = p_user_id
  for update;
  if not found then
    return pg_catalog.jsonb_build_object('outcome', 'not_found');
  end if;

  select delivery.* into v_delivery
  from public.reminder_deliveries as delivery
  where delivery.request_owner_id = p_user_id
    and delivery.manual_idempotency_key = p_idempotency_key
    and delivery.reminder_type = 'manual_notification'
  for update;

  if found and v_delivery.action_item_id <> p_action_item_id then
    return pg_catalog.jsonb_build_object('outcome', 'incompatible');
  end if;
  if found and v_delivery.status = 'processing'
    and v_delivery.lease_expires_at <= pg_catalog.now()
    and v_delivery.provider_attempted_at is not null then
    update public.reminder_deliveries
    set status = 'unknown', claim_token = null, lease_expires_at = null,
        failure_code = 'worker_lost_after_send_started',
        error_message = 'Delivery requires reconciliation before another send.'
    where id = v_delivery.id
    returning * into v_delivery;
  end if;
  if found and (
    v_delivery.status in ('sent', 'unknown', 'cancelled')
    or (v_delivery.status = 'processing' and v_delivery.lease_expires_at > pg_catalog.now())
    or (v_delivery.status = 'failed' and v_delivery.next_attempt_at is null)
    or (v_delivery.status = 'failed' and v_delivery.next_attempt_at > pg_catalog.now())
    or v_delivery.attempt_count >= p_max_attempts
  ) then
    return pg_catalog.jsonb_build_object(
      'outcome', 'existing', 'delivery_id', v_delivery.id,
      'status', v_delivery.status, 'attempt_count', v_delivery.attempt_count,
      'next_attempt_at', v_delivery.next_attempt_at
    );
  end if;

  if v_action.status = 'completed'
    or nullif(pg_catalog.btrim(coalesce(v_action.assignee_email, '')), '') is null
    or (v_action.source = 'ai_generated' and v_action.review_status <> 'confirmed') then
    return pg_catalog.jsonb_build_object('outcome', 'ineligible');
  end if;

  if not found then
    v_dedupe_key := 'manual:' || p_user_id::text || ':'
      || p_action_item_id::text || ':' || p_idempotency_key::text;
    insert into public.reminder_deliveries (
      action_item_id, request_owner_id, manual_idempotency_key,
      reminder_type, dedupe_key, status
    ) values (
      p_action_item_id, p_user_id, p_idempotency_key,
      'manual_notification', v_dedupe_key, 'pending'
    ) on conflict (request_owner_id, manual_idempotency_key)
      where reminder_type = 'manual_notification' and manual_idempotency_key is not null
    do nothing;

    select delivery.* into v_delivery
    from public.reminder_deliveries as delivery
    where delivery.request_owner_id = p_user_id
      and delivery.manual_idempotency_key = p_idempotency_key
      and delivery.reminder_type = 'manual_notification'
    for update;
    if v_delivery.action_item_id <> p_action_item_id then
      return pg_catalog.jsonb_build_object('outcome', 'incompatible');
    end if;
  end if;

  update public.reminder_deliveries
  set status = 'processing', attempt_count = attempt_count + 1,
      last_attempt_at = pg_catalog.now(), next_attempt_at = null,
      claimed_at = pg_catalog.now(), claim_token = p_claim_token,
      lease_expires_at = pg_catalog.now() + pg_catalog.make_interval(secs => p_lease_seconds),
      provider_attempted_at = null, failure_code = null, error_message = null
  where id = v_delivery.id
  returning * into v_delivery;

  return pg_catalog.jsonb_build_object(
    'outcome', 'claimed', 'delivery_id', v_delivery.id,
    'claim_token', v_delivery.claim_token, 'status', v_delivery.status,
    'attempt_count', v_delivery.attempt_count
  );
end;
$function$;

create or replace function public.claim_scheduled_reminders(
  p_logical_date date,
  p_batch_size integer,
  p_lease_seconds integer,
  p_max_attempts integer
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $function$
declare
  v_claims jsonb;
begin
  if p_logical_date is null or p_batch_size not between 1 and 200
    or p_lease_seconds not between 60 and 900
    or p_max_attempts not between 1 and 10 then
    raise exception 'Invalid scheduled reminder claim input.' using errcode = '22023';
  end if;

  update public.reminder_deliveries
  set status = 'unknown', claim_token = null, lease_expires_at = null,
      failure_code = 'worker_lost_after_send_started',
      error_message = 'Delivery requires reconciliation before another send.'
  where status = 'processing'
    and lease_expires_at <= pg_catalog.now()
    and provider_attempted_at is not null;

  with candidates as (
    select action.id as action_item_id, action.user_id,
      case
        when action.deadline = p_logical_date then 'deadline'
        when action.start_date = p_logical_date then 'start_date'
        when action.deadline = p_logical_date + 1 then 'before_deadline'
      end as reminder_type
    from public.action_items as action
    where action.status <> 'completed'
      and (action.source <> 'ai_generated' or action.review_status = 'confirmed')
      and (
        action.deadline = p_logical_date
        or action.start_date = p_logical_date
        or action.deadline = p_logical_date + 1
      )
      and not exists (
        select 1 from public.reminder_deliveries as existing
        where existing.dedupe_key = 'scheduled:' || action.id::text || ':'
          || case
            when action.deadline = p_logical_date then 'deadline'
            when action.start_date = p_logical_date then 'start_date'
            when action.deadline = p_logical_date + 1 then 'before_deadline'
          end || ':' || p_logical_date::text
      )
    order by action.id
    limit p_batch_size
  )
  insert into public.reminder_deliveries (
    action_item_id, request_owner_id, reminder_type,
    scheduled_for, dedupe_key, status
  )
  select candidate.action_item_id, candidate.user_id, candidate.reminder_type,
    p_logical_date,
    'scheduled:' || candidate.action_item_id::text || ':'
      || candidate.reminder_type || ':' || p_logical_date::text,
    'pending'
  from candidates as candidate
  on conflict (dedupe_key) do nothing;

  with claimable as (
    select delivery.id
    from public.reminder_deliveries as delivery
    join public.action_items as action on action.id = delivery.action_item_id
    where delivery.attempt_count < p_max_attempts
      and action.status <> 'completed'
      and nullif(pg_catalog.btrim(coalesce(action.assignee_email, '')), '') is not null
      and (action.source <> 'ai_generated' or action.review_status = 'confirmed')
      and (
        delivery.status = 'pending'
        or (delivery.status = 'failed' and delivery.next_attempt_at <= pg_catalog.now())
        or (
          delivery.status = 'processing'
          and delivery.lease_expires_at <= pg_catalog.now()
          and delivery.provider_attempted_at is null
        )
      )
    order by coalesce(delivery.next_attempt_at, delivery.created_at), delivery.id
    for update of delivery skip locked
    limit p_batch_size
  ), claimed as (
    update public.reminder_deliveries as delivery
    set status = 'processing', attempt_count = delivery.attempt_count + 1,
        last_attempt_at = pg_catalog.now(), next_attempt_at = null,
        claimed_at = pg_catalog.now(), claim_token = pg_catalog.gen_random_uuid(),
        lease_expires_at = pg_catalog.now() + pg_catalog.make_interval(secs => p_lease_seconds),
        provider_attempted_at = null, failure_code = null, error_message = null
    from claimable where delivery.id = claimable.id
    returning delivery.id, delivery.claim_token, delivery.attempt_count
  )
  select coalesce(pg_catalog.jsonb_agg(
    pg_catalog.jsonb_build_object(
      'delivery_id', claimed.id, 'claim_token', claimed.claim_token,
      'attempt_count', claimed.attempt_count
    ) order by claimed.id
  ), '[]'::jsonb) into v_claims
  from claimed;
  return v_claims;
end;
$function$;

create or replace function public.prepare_reminder_delivery(
  p_delivery_id uuid,
  p_claim_token uuid,
  p_sibling_group_id uuid,
  p_expires_at timestamptz,
  p_token_hashes jsonb
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $function$
declare
  v_delivery public.reminder_deliveries%rowtype;
  v_action public.action_items%rowtype;
  v_allowed_targets jsonb;
  v_applies boolean;
begin
  if p_delivery_id is null or p_claim_token is null or p_sibling_group_id is null
    or p_expires_at <= pg_catalog.now()
    or p_expires_at > pg_catalog.now() + interval '168 hours'
    or pg_catalog.jsonb_typeof(p_token_hashes) <> 'array'
    or pg_catalog.jsonb_array_length(p_token_hashes) <> 2 then
    raise exception 'Invalid reminder preparation input.' using errcode = '22023';
  end if;

  select delivery.* into v_delivery
  from public.reminder_deliveries as delivery
  where delivery.id = p_delivery_id and delivery.status = 'processing'
    and delivery.claim_token = p_claim_token
    and delivery.lease_expires_at > pg_catalog.now()
  for update;
  if not found then
    return pg_catalog.jsonb_build_object('outcome', 'claim_lost');
  end if;

  select action.* into v_action
  from public.action_items as action
  where action.id = v_delivery.action_item_id
  for update;

  v_applies := found
    and v_action.status <> 'completed'
    and nullif(pg_catalog.btrim(coalesce(v_action.assignee_email, '')), '') is not null
    and (v_action.source <> 'ai_generated' or v_action.review_status = 'confirmed')
    and (
      v_delivery.reminder_type = 'manual_notification'
      or (v_delivery.reminder_type = 'deadline' and v_action.deadline = v_delivery.scheduled_for)
      or (v_delivery.reminder_type = 'start_date' and v_action.start_date = v_delivery.scheduled_for)
      or (
        v_delivery.reminder_type = 'before_deadline'
        and v_action.deadline = v_delivery.scheduled_for + 1
      )
    );
  if not v_applies then
    update public.reminder_deliveries
    set status = 'cancelled', claim_token = null, lease_expires_at = null,
        next_attempt_at = null, failure_code = 'no_longer_eligible',
        error_message = null
    where id = v_delivery.id;
    update public.action_status_tokens
    set revoked_at = coalesce(revoked_at, pg_catalog.now())
    where reminder_delivery_id = v_delivery.id and used_at is null;
    return pg_catalog.jsonb_build_object('outcome', 'cancelled');
  end if;

  if exists (
    select 1 from pg_catalog.jsonb_array_elements(p_token_hashes) as token(value)
    where token.value ->> 'target_status' not in ('in_progress', 'completed')
      or coalesce(token.value ->> 'token_hash', '') !~ '^[0-9a-f]{64}$'
  ) or (
    select pg_catalog.count(distinct token.value ->> 'target_status')
    from pg_catalog.jsonb_array_elements(p_token_hashes) as token(value)
  ) <> 2 then
    raise exception 'Invalid status token hashes.' using errcode = '22023';
  end if;

  update public.action_status_tokens
  set revoked_at = coalesce(revoked_at, pg_catalog.now())
  where reminder_delivery_id = v_delivery.id and used_at is null;

  insert into public.action_status_tokens (
    action_item_id, reminder_delivery_id, target_status,
    token_hash, sibling_group_id, expires_at
  )
  select v_action.id, v_delivery.id, token.value ->> 'target_status',
    pg_catalog.decode(token.value ->> 'token_hash', 'hex'),
    p_sibling_group_id, p_expires_at
  from pg_catalog.jsonb_array_elements(p_token_hashes) as token(value)
  where token.value ->> 'target_status' = 'completed'
    or (token.value ->> 'target_status' = 'in_progress' and v_action.status = 'not_started');

  select pg_catalog.jsonb_agg(status_value order by status_value)
  into v_allowed_targets
  from (
    select token.target_status as status_value
    from public.action_status_tokens as token
    where token.reminder_delivery_id = v_delivery.id
      and token.sibling_group_id = p_sibling_group_id
      and token.revoked_at is null
  ) as allowed;

  return pg_catalog.jsonb_build_object(
    'outcome', 'prepared', 'delivery_id', v_delivery.id,
    'action_item_id', v_action.id, 'reminder_type', v_delivery.reminder_type,
    'scheduled_for', v_delivery.scheduled_for,
    'attempt_count', v_delivery.attempt_count, 'title', v_action.title,
    'assignee', v_action.assignee, 'recipient_email', v_action.assignee_email,
    'start_date', v_action.start_date, 'deadline', v_action.deadline,
    'current_status', v_action.status,
    'allowed_targets', coalesce(v_allowed_targets, '[]'::jsonb)
  );
end;
$function$;

create or replace function public.mark_reminder_send_started(
  p_delivery_id uuid,
  p_claim_token uuid
)
returns boolean
language sql
security definer
set search_path = ''
as $function$
  update public.reminder_deliveries as delivery
  set provider_attempted_at = pg_catalog.now()
  from public.action_items as action
  where delivery.id = p_delivery_id
    and action.id = delivery.action_item_id
    and delivery.status = 'processing'
    and delivery.claim_token = p_claim_token
    and delivery.lease_expires_at > pg_catalog.now()
    and delivery.provider_attempted_at is null
    and action.status <> 'completed'
    and nullif(pg_catalog.btrim(coalesce(action.assignee_email, '')), '') is not null
    and (action.source <> 'ai_generated' or action.review_status = 'confirmed')
  returning true;
$function$;

revoke all on function public.claim_manual_reminder(uuid,uuid,uuid,uuid,integer,integer)
  from public, anon, authenticated;
revoke all on function public.claim_scheduled_reminders(date,integer,integer,integer)
  from public, anon, authenticated;
revoke all on function public.prepare_reminder_delivery(uuid,uuid,uuid,timestamptz,jsonb)
  from public, anon, authenticated;
revoke all on function public.mark_reminder_send_started(uuid,uuid)
  from public, anon, authenticated;

grant execute on function public.claim_manual_reminder(uuid,uuid,uuid,uuid,integer,integer)
  to service_role;
grant execute on function public.claim_scheduled_reminders(date,integer,integer,integer)
  to service_role;
grant execute on function public.prepare_reminder_delivery(uuid,uuid,uuid,timestamptz,jsonb)
  to service_role;
grant execute on function public.mark_reminder_send_started(uuid,uuid)
  to service_role;

commit;
