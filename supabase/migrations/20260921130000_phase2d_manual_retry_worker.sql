begin;

-- The worker owns all due retry work, including a manual request whose first
-- provider attempt received a retryable response. New scheduled rows are still
-- created from the explicit logical date before the common backlog is claimed.
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

  -- Any worker lost after recording that a provider call began has an
  -- ambiguous outcome. This applies equally to scheduled and manual delivery.
  update public.reminder_deliveries
  set
    status = 'unknown',
    claim_token = null,
    lease_expires_at = null,
    failure_code = 'worker_lost_after_send_started',
    error_message = 'Delivery requires reconciliation before another send.'
  where status = 'processing'
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
    where delivery.attempt_count < p_max_attempts
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

revoke all on function public.claim_scheduled_reminders(date, integer, integer, integer)
  from public, anon, authenticated;
grant execute on function public.claim_scheduled_reminders(date, integer, integer, integer)
  to service_role;

commit;
