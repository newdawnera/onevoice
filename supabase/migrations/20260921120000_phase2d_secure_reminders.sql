begin;

-- Phase 2D turns reminder_deliveries into a durable lease-based state machine.
alter table public.reminder_deliveries
  add column if not exists request_owner_id uuid references auth.users (id) on delete cascade,
  add column if not exists manual_idempotency_key uuid,
  add column if not exists attempt_count integer not null default 0,
  add column if not exists last_attempt_at timestamptz,
  add column if not exists next_attempt_at timestamptz,
  add column if not exists claimed_at timestamptz,
  add column if not exists claim_token uuid,
  add column if not exists lease_expires_at timestamptz,
  add column if not exists provider_attempted_at timestamptz,
  add column if not exists failure_code text,
  add column if not exists updated_at timestamptz not null default pg_catalog.now();

update public.reminder_deliveries as delivery
set
  dedupe_key = coalesce(delivery.dedupe_key, 'legacy:' || delivery.id::text),
  request_owner_id = coalesce(delivery.request_owner_id, action.user_id),
  provider_message_id = left(delivery.provider_message_id, 255),
  error_message = left(delivery.error_message, 500)
from public.action_items as action
where action.id = delivery.action_item_id;

alter table public.reminder_deliveries
  alter column dedupe_key set not null;

alter table public.reminder_deliveries
  drop constraint if exists reminder_deliveries_status_check;

alter table public.reminder_deliveries
  add constraint reminder_deliveries_status_check
    check (status in ('pending', 'processing', 'sent', 'failed', 'unknown', 'cancelled')),
  add constraint reminder_deliveries_attempt_count_check
    check (attempt_count >= 0 and attempt_count <= 100),
  add constraint reminder_deliveries_dedupe_key_length_check
    check (char_length(dedupe_key) between 1 and 500),
  add constraint reminder_deliveries_provider_message_id_length_check
    check (provider_message_id is null or char_length(provider_message_id) <= 255),
  add constraint reminder_deliveries_failure_code_length_check
    check (failure_code is null or char_length(failure_code) <= 64),
  add constraint reminder_deliveries_error_message_length_check
    check (error_message is null or char_length(error_message) <= 500),
  add constraint reminder_deliveries_sent_state_check
    check ((status = 'sent') = (sent_at is not null)),
  add constraint reminder_deliveries_processing_lease_check
    check (
      (status = 'processing' and claim_token is not null and claimed_at is not null and lease_expires_at is not null)
      or
      (status <> 'processing' and claim_token is null and lease_expires_at is null)
    ),
  add constraint reminder_deliveries_manual_identity_check
    check (
      (reminder_type = 'manual_notification' and request_owner_id is not null)
      or
      (reminder_type <> 'manual_notification' and scheduled_for is not null)
    );

create unique index reminder_deliveries_manual_request_key
  on public.reminder_deliveries (request_owner_id, manual_idempotency_key)
  where reminder_type = 'manual_notification' and manual_idempotency_key is not null;

create unique index reminder_deliveries_active_claim_key
  on public.reminder_deliveries (claim_token)
  where claim_token is not null;

create index reminder_deliveries_due_claim_idx
  on public.reminder_deliveries (status, next_attempt_at, lease_expires_at, created_at)
  where status in ('pending', 'processing', 'failed');

create index reminder_deliveries_action_created_idx
  on public.reminder_deliveries (action_item_id, created_at desc);

create index reminder_deliveries_cleanup_idx
  on public.reminder_deliveries (updated_at)
  where status in ('sent', 'failed', 'unknown', 'cancelled');

drop trigger if exists reminder_deliveries_set_updated_at on public.reminder_deliveries;
create trigger reminder_deliveries_set_updated_at
before update on public.reminder_deliveries
for each row execute function private.set_updated_at();

create table public.action_status_tokens (
  id uuid primary key default pg_catalog.gen_random_uuid(),
  action_item_id uuid not null references public.action_items (id) on delete cascade,
  reminder_delivery_id uuid references public.reminder_deliveries (id) on delete cascade,
  target_status text not null,
  token_hash bytea not null,
  sibling_group_id uuid not null,
  expires_at timestamptz not null,
  created_at timestamptz not null default pg_catalog.now(),
  used_at timestamptz,
  revoked_at timestamptz,
  constraint action_status_tokens_target_check
    check (target_status in ('in_progress', 'completed')),
  constraint action_status_tokens_hash_length_check
    check (pg_catalog.octet_length(token_hash) = 32),
  constraint action_status_tokens_expiry_check
    check (expires_at > created_at),
  constraint action_status_tokens_used_revoked_check
    check (used_at is null or revoked_at is null),
  constraint action_status_tokens_hash_key unique (token_hash)
);

create index action_status_tokens_action_idx
  on public.action_status_tokens (action_item_id, created_at desc);

create index action_status_tokens_delivery_idx
  on public.action_status_tokens (reminder_delivery_id)
  where reminder_delivery_id is not null;

create index action_status_tokens_expiry_idx
  on public.action_status_tokens (expires_at)
  where used_at is null and revoked_at is null;

create index action_status_tokens_sibling_idx
  on public.action_status_tokens (sibling_group_id)
  where used_at is null and revoked_at is null;

alter table public.action_status_tokens enable row level security;
revoke all on table public.action_status_tokens from public, anon, authenticated;
grant select, insert, update, delete on table public.action_status_tokens to service_role;

-- Browser roles already have no reminder-delivery privileges. Reassert this
-- boundary here so a later dashboard grant cannot be mistaken for Phase 2D.
alter table public.reminder_deliveries enable row level security;
revoke all on table public.reminder_deliveries from public, anon, authenticated;

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
    set
      status = 'unknown',
      claim_token = null,
      lease_expires_at = null,
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
      'outcome', 'existing',
      'delivery_id', v_delivery.id,
      'status', v_delivery.status,
      'attempt_count', v_delivery.attempt_count,
      'next_attempt_at', v_delivery.next_attempt_at
    );
  end if;

  if v_action.status = 'completed'
    or nullif(pg_catalog.btrim(coalesce(v_action.assignee_email, '')), '') is null then
    return pg_catalog.jsonb_build_object('outcome', 'ineligible');
  end if;

  if not found then
    v_dedupe_key := 'manual:' || p_user_id::text || ':'
      || p_action_item_id::text || ':' || p_idempotency_key::text;
    insert into public.reminder_deliveries (
      action_item_id,
      request_owner_id,
      manual_idempotency_key,
      reminder_type,
      dedupe_key,
      status
    ) values (
      p_action_item_id,
      p_user_id,
      p_idempotency_key,
      'manual_notification',
      v_dedupe_key,
      'pending'
    )
    on conflict (request_owner_id, manual_idempotency_key)
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
  set
    status = 'processing',
    attempt_count = attempt_count + 1,
    last_attempt_at = pg_catalog.now(),
    next_attempt_at = null,
    claimed_at = pg_catalog.now(),
    claim_token = p_claim_token,
    lease_expires_at = pg_catalog.now() + pg_catalog.make_interval(secs => p_lease_seconds),
    provider_attempted_at = null,
    failure_code = null,
    error_message = null
  where id = v_delivery.id
  returning * into v_delivery;

  return pg_catalog.jsonb_build_object(
    'outcome', 'claimed',
    'delivery_id', v_delivery.id,
    'claim_token', v_delivery.claim_token,
    'status', v_delivery.status,
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
  set
    status = 'unknown',
    claim_token = null,
    lease_expires_at = null,
    failure_code = 'worker_lost_after_send_started',
    error_message = 'Delivery requires reconciliation before another send.'
  where reminder_type <> 'manual_notification'
    and status = 'processing'
    and lease_expires_at <= pg_catalog.now()
    and provider_attempted_at is not null;

  with candidates as (
    select
      action.id as action_item_id,
      action.user_id,
      case
        when action.deadline = p_logical_date then 'deadline'
        when action.start_date = p_logical_date then 'start_date'
        when action.deadline = p_logical_date + 1 then 'before_deadline'
      end as reminder_type
    from public.action_items as action
    where action.status <> 'completed'
      and (
        action.deadline = p_logical_date
        or action.start_date = p_logical_date
        or action.deadline = p_logical_date + 1
      )
      and not exists (
        select 1
        from public.reminder_deliveries as existing
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
    action_item_id,
    request_owner_id,
    reminder_type,
    scheduled_for,
    dedupe_key,
    status
  )
  select
    candidate.action_item_id,
    candidate.user_id,
    candidate.reminder_type,
    p_logical_date,
    'scheduled:' || candidate.action_item_id::text || ':'
      || candidate.reminder_type || ':' || p_logical_date::text,
    'pending'
  from candidates as candidate
  on conflict (dedupe_key) do nothing;

  with claimable as (
    select delivery.id
    from public.reminder_deliveries as delivery
    where delivery.reminder_type <> 'manual_notification'
      and delivery.attempt_count < p_max_attempts
      and (
        delivery.status = 'pending'
        or (
          delivery.status = 'failed'
          and delivery.next_attempt_at is not null
          and delivery.next_attempt_at <= pg_catalog.now()
        )
        or (
        delivery.status = 'processing'
          and delivery.lease_expires_at <= pg_catalog.now()
          and delivery.provider_attempted_at is null
        )
      )
    order by coalesce(delivery.next_attempt_at, delivery.created_at), delivery.id
    for update skip locked
    limit p_batch_size
  ), claimed as (
    update public.reminder_deliveries as delivery
    set
      status = 'processing',
      attempt_count = delivery.attempt_count + 1,
      last_attempt_at = pg_catalog.now(),
      next_attempt_at = null,
      claimed_at = pg_catalog.now(),
      claim_token = pg_catalog.gen_random_uuid(),
      lease_expires_at = pg_catalog.now() + pg_catalog.make_interval(secs => p_lease_seconds),
      provider_attempted_at = null,
      failure_code = null,
      error_message = null
    from claimable
    where delivery.id = claimable.id
    returning delivery.id, delivery.claim_token, delivery.attempt_count
  )
  select coalesce(
    pg_catalog.jsonb_agg(
      pg_catalog.jsonb_build_object(
        'delivery_id', claimed.id,
        'claim_token', claimed.claim_token,
        'attempt_count', claimed.attempt_count
      ) order by claimed.id
    ),
    '[]'::jsonb
  ) into v_claims
  from claimed;

  return v_claims;
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
  update public.reminder_deliveries
  set provider_attempted_at = pg_catalog.now()
  where id = p_delivery_id
    and status = 'processing'
    and claim_token = p_claim_token
    and lease_expires_at > pg_catalog.now()
    and provider_attempted_at is null
  returning true;
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
  where delivery.id = p_delivery_id
    and delivery.status = 'processing'
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
    set
      status = 'cancelled',
      claim_token = null,
      lease_expires_at = null,
      next_attempt_at = null,
      failure_code = 'no_longer_eligible',
      error_message = null
    where id = v_delivery.id;
    update public.action_status_tokens
    set revoked_at = coalesce(revoked_at, pg_catalog.now())
    where reminder_delivery_id = v_delivery.id and used_at is null;
    return pg_catalog.jsonb_build_object('outcome', 'cancelled');
  end if;

  if exists (
    select 1
    from pg_catalog.jsonb_array_elements(p_token_hashes) as token(value)
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
    action_item_id,
    reminder_delivery_id,
    target_status,
    token_hash,
    sibling_group_id,
    expires_at
  )
  select
    v_action.id,
    v_delivery.id,
    token.value ->> 'target_status',
    pg_catalog.decode(token.value ->> 'token_hash', 'hex'),
    p_sibling_group_id,
    p_expires_at
  from pg_catalog.jsonb_array_elements(p_token_hashes) as token(value)
  where token.value ->> 'target_status' = 'completed'
    or (
      token.value ->> 'target_status' = 'in_progress'
      and v_action.status = 'not_started'
    );

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
    'outcome', 'prepared',
    'delivery_id', v_delivery.id,
    'action_item_id', v_action.id,
    'reminder_type', v_delivery.reminder_type,
    'scheduled_for', v_delivery.scheduled_for,
    'attempt_count', v_delivery.attempt_count,
    'title', v_action.title,
    'assignee', v_action.assignee,
    'recipient_email', v_action.assignee_email,
    'start_date', v_action.start_date,
    'deadline', v_action.deadline,
    'current_status', v_action.status,
    'allowed_targets', coalesce(v_allowed_targets, '[]'::jsonb)
  );
end;
$function$;

create or replace function public.finalize_reminder_delivery(
  p_delivery_id uuid,
  p_claim_token uuid,
  p_outcome text,
  p_provider_message_id text default null,
  p_failure_code text default null,
  p_error_message text default null,
  p_next_attempt_at timestamptz default null
)
returns boolean
language plpgsql
security definer
set search_path = ''
as $function$
declare
  v_updated boolean;
begin
  if p_outcome not in ('sent', 'failed', 'unknown', 'cancelled')
    or char_length(coalesce(p_provider_message_id, '')) > 255
    or char_length(coalesce(p_failure_code, '')) > 64
    or char_length(coalesce(p_error_message, '')) > 500 then
    raise exception 'Invalid reminder finalization input.' using errcode = '22023';
  end if;

  update public.reminder_deliveries
  set
    status = p_outcome,
    provider_message_id = nullif(p_provider_message_id, ''),
    failure_code = nullif(p_failure_code, ''),
    error_message = nullif(p_error_message, ''),
    next_attempt_at = case when p_outcome = 'failed' then p_next_attempt_at else null end,
    sent_at = case when p_outcome = 'sent' then pg_catalog.now() else null end,
    claim_token = null,
    lease_expires_at = null
  where id = p_delivery_id
    and status = 'processing'
    and claim_token = p_claim_token;

  v_updated := found;
  if v_updated and p_outcome in ('failed', 'cancelled') then
    update public.action_status_tokens
    set revoked_at = coalesce(revoked_at, pg_catalog.now())
    where reminder_delivery_id = p_delivery_id and used_at is null;
  end if;
  return v_updated;
end;
$function$;

create or replace function public.inspect_action_status_token(
  p_token_hash text,
  p_now timestamptz default pg_catalog.now()
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $function$
declare
  v_target text;
begin
  if coalesce(p_token_hash, '') !~ '^[0-9a-f]{64}$' then
    return pg_catalog.jsonb_build_object('valid', false);
  end if;

  select token.target_status into v_target
  from public.action_status_tokens as token
  join public.action_items as action on action.id = token.action_item_id
  where token.token_hash = pg_catalog.decode(p_token_hash, 'hex')
    and token.used_at is null
    and token.revoked_at is null
    and token.expires_at > p_now
    and (
      (action.status = 'not_started' and token.target_status in ('in_progress', 'completed'))
      or (action.status = 'in_progress' and token.target_status = 'completed')
    );

  if not found then
    return pg_catalog.jsonb_build_object('valid', false);
  end if;
  return pg_catalog.jsonb_build_object('valid', true, 'target_status', v_target);
end;
$function$;

create or replace function public.consume_action_status_token(
  p_token_hash text,
  p_now timestamptz default pg_catalog.now()
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $function$
declare
  v_token public.action_status_tokens%rowtype;
  v_action public.action_items%rowtype;
begin
  if coalesce(p_token_hash, '') !~ '^[0-9a-f]{64}$' then
    return pg_catalog.jsonb_build_object('updated', false);
  end if;

  select token.* into v_token
  from public.action_status_tokens as token
  where token.token_hash = pg_catalog.decode(p_token_hash, 'hex')
  for update;

  if not found or v_token.used_at is not null or v_token.revoked_at is not null
    or v_token.expires_at <= p_now then
    return pg_catalog.jsonb_build_object('updated', false);
  end if;

  select action.* into v_action
  from public.action_items as action
  where action.id = v_token.action_item_id
  for update;

  if not found or not (
    (v_action.status = 'not_started' and v_token.target_status in ('in_progress', 'completed'))
    or (v_action.status = 'in_progress' and v_token.target_status = 'completed')
  ) then
    return pg_catalog.jsonb_build_object('updated', false);
  end if;

  update public.action_items
  set status = v_token.target_status
  where id = v_action.id;

  update public.action_status_tokens
  set used_at = p_now
  where id = v_token.id;

  update public.action_status_tokens
  set revoked_at = p_now
  where sibling_group_id = v_token.sibling_group_id
    and id <> v_token.id
    and used_at is null
    and revoked_at is null;

  return pg_catalog.jsonb_build_object(
    'updated', true,
    'target_status', v_token.target_status
  );
end;
$function$;

revoke all on function public.claim_manual_reminder(uuid, uuid, uuid, uuid, integer, integer)
  from public, anon, authenticated;
revoke all on function public.claim_scheduled_reminders(date, integer, integer, integer)
  from public, anon, authenticated;
revoke all on function public.prepare_reminder_delivery(uuid, uuid, uuid, timestamptz, jsonb)
  from public, anon, authenticated;
revoke all on function public.mark_reminder_send_started(uuid, uuid)
  from public, anon, authenticated;
revoke all on function public.finalize_reminder_delivery(uuid, uuid, text, text, text, text, timestamptz)
  from public, anon, authenticated;
revoke all on function public.inspect_action_status_token(text, timestamptz)
  from public, anon, authenticated;
revoke all on function public.consume_action_status_token(text, timestamptz)
  from public, anon, authenticated;

grant execute on function public.claim_manual_reminder(uuid, uuid, uuid, uuid, integer, integer)
  to service_role;
grant execute on function public.claim_scheduled_reminders(date, integer, integer, integer)
  to service_role;
grant execute on function public.prepare_reminder_delivery(uuid, uuid, uuid, timestamptz, jsonb)
  to service_role;
grant execute on function public.mark_reminder_send_started(uuid, uuid)
  to service_role;
grant execute on function public.finalize_reminder_delivery(uuid, uuid, text, text, text, text, timestamptz)
  to service_role;
grant execute on function public.inspect_action_status_token(text, timestamptz)
  to service_role;
grant execute on function public.consume_action_status_token(text, timestamptz)
  to service_role;

commit;
