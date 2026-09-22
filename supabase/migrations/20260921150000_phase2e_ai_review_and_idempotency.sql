begin;

alter table public.meeting_records
  add column prompt_version text;

alter table public.meeting_records
  add constraint meeting_records_prompt_version_length_check
    check (prompt_version is null or char_length(prompt_version) between 1 and 50);

alter table public.action_items
  add column review_status text not null default 'confirmed',
  add column reviewed_at timestamptz default pg_catalog.now(),
  add column ai_evidence text;

update public.action_items
set review_status = case when source = 'ai_generated' then 'pending' else 'confirmed' end,
    reviewed_at = case when source = 'ai_generated' then null else coalesce(reviewed_at, created_at) end;

alter table public.action_items
  add constraint action_items_review_status_check
    check (review_status in ('pending', 'confirmed', 'rejected')),
  add constraint action_items_review_timestamp_check
    check (
      (review_status = 'pending' and reviewed_at is null)
      or (review_status in ('confirmed', 'rejected') and reviewed_at is not null)
    ),
  add constraint action_items_ai_evidence_length_check
    check (ai_evidence is null or char_length(ai_evidence) <= 500),
  add constraint action_items_manual_review_check
    check (source <> 'manual' or review_status = 'confirmed');

create index action_items_review_eligibility_idx
  on public.action_items (review_status, status, deadline, start_date)
  where source = 'ai_generated';

-- Make the safe review state depend on server-owned provenance. Manual rows
-- remain immediately usable; every AI row starts pending even if a future
-- privileged caller forgets to provide the review columns explicitly.
create or replace function private.set_action_item_review_defaults()
returns trigger
language plpgsql
set search_path = ''
as $function$
begin
  if new.source = 'ai_generated' then
    new.review_status := 'pending';
    new.reviewed_at := null;
  else
    new.review_status := 'confirmed';
    new.reviewed_at := coalesce(new.reviewed_at, pg_catalog.now());
  end if;
  return new;
end;
$function$;

revoke all on function private.set_action_item_review_defaults()
from public, anon, authenticated;

create trigger action_items_set_review_defaults
before insert on public.action_items
for each row execute function private.set_action_item_review_defaults();

-- A direct browser update can edit ordinary action fields, but any material
-- change to a confirmed AI proposal invalidates the earlier approval.
create or replace function private.enforce_action_item_immutability()
returns trigger
language plpgsql
set search_path = ''
as $function$
begin
  if new.id is distinct from old.id
    or new.user_id is distinct from old.user_id
    or new.meeting_id is distinct from old.meeting_id
    or new.source is distinct from old.source
    or new.created_at is distinct from old.created_at
    or new.ai_evidence is distinct from old.ai_evidence then
    raise exception 'Immutable action-item fields cannot be changed.'
      using errcode = '42501';
  end if;

  if old.source = 'ai_generated' and (
    new.title is distinct from old.title
    or new.assignee is distinct from old.assignee
    or new.assignee_email is distinct from old.assignee_email
    or new.start_date is distinct from old.start_date
    or new.deadline is distinct from old.deadline
  ) then
    new.review_status := 'pending';
    new.reviewed_at := null;
  end if;
  return new;
end;
$function$;

revoke all on function private.enforce_action_item_immutability()
from public, anon, authenticated;

-- When approval is absent, clearly unsent work is cancelled. A worker that
-- already recorded provider contact becomes unknown and is never blindly sent
-- again. Sent/unknown history is preserved.
create or replace function private.invalidate_ai_action_reminders()
returns trigger
language plpgsql
set search_path = ''
as $function$
begin
  if new.source = 'ai_generated' and new.review_status <> 'confirmed' then
    update public.reminder_deliveries
    set
      status = case
        when status = 'processing' and provider_attempted_at is not null then 'unknown'
        else 'cancelled'
      end,
      claim_token = null,
      lease_expires_at = null,
      next_attempt_at = null,
      failure_code = case
        when status = 'processing' and provider_attempted_at is not null
          then 'review_revoked_after_send_started'
        else 'action_review_required'
      end,
      error_message = case
        when status = 'processing' and provider_attempted_at is not null
          then 'Delivery requires reconciliation after action review changed.'
        else null
      end
    where action_item_id = new.id
      and (
        status in ('pending', 'failed')
        or (status = 'processing')
      );

    update public.action_status_tokens
    set revoked_at = coalesce(revoked_at, pg_catalog.now())
    where action_item_id = new.id
      and used_at is null
      and revoked_at is null;
  end if;
  return new;
end;
$function$;

revoke all on function private.invalidate_ai_action_reminders()
from public, anon, authenticated;

create trigger action_items_invalidate_unreviewed_reminders
after update of title, assignee, assignee_email, start_date, deadline, review_status
on public.action_items
for each row execute function private.invalidate_ai_action_reminders();

-- Apply the same conservative transition to work that existed before this
-- migration. Sent and already-unknown delivery history remains untouched.
update public.reminder_deliveries as delivery
set
  status = case
    when delivery.status = 'processing' and delivery.provider_attempted_at is not null
      then 'unknown'
    else 'cancelled'
  end,
  claim_token = null,
  lease_expires_at = null,
  next_attempt_at = null,
  failure_code = case
    when delivery.status = 'processing' and delivery.provider_attempted_at is not null
      then 'review_required_after_send_started'
    else 'action_review_required'
  end,
  error_message = case
    when delivery.status = 'processing' and delivery.provider_attempted_at is not null
      then 'Delivery requires reconciliation after review migration.'
    else null
  end
from public.action_items as action
where action.id = delivery.action_item_id
  and action.source = 'ai_generated'
  and action.review_status <> 'confirmed'
  and delivery.status in ('pending', 'failed', 'processing');

update public.action_status_tokens as token
set revoked_at = coalesce(token.revoked_at, pg_catalog.now())
from public.action_items as action
where action.id = token.action_item_id
  and action.source = 'ai_generated'
  and action.review_status <> 'confirmed'
  and token.used_at is null
  and token.revoked_at is null;

-- Browser clients can no longer assert provider provenance or create
-- ai_generated actions through the Phase 2C RPC.
revoke all on function public.save_meeting_with_actions(
  uuid, text, text, text, text, text, text, text, text, text, text, text, jsonb
) from public, anon, authenticated;

create table private.ai_generation_requests (
  id uuid primary key default pg_catalog.gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  operation text not null,
  idempotency_key uuid not null,
  request_hash text not null,
  status text not null default 'processing',
  claim_token uuid,
  lease_expires_at timestamptz,
  provider_started_at timestamptz,
  provider text,
  model text,
  prompt_version text,
  finish_reason text,
  meeting_id uuid references public.meeting_records (id) on delete set null,
  input_tokens integer,
  output_tokens integer,
  provider_call_count integer,
  failure_category text,
  created_at timestamptz not null default pg_catalog.now(),
  updated_at timestamptz not null default pg_catalog.now(),
  completed_at timestamptz,
  constraint ai_generation_requests_user_operation_key
    unique (user_id, operation, idempotency_key),
  constraint ai_generation_requests_operation_check
    check (operation = 'meeting_generation'),
  constraint ai_generation_requests_hash_check
    check (request_hash ~ '^[0-9a-f]{64}$'),
  constraint ai_generation_requests_status_check
    check (status in ('processing', 'completed', 'failed', 'unknown')),
  constraint ai_generation_requests_claim_check
    check (
      (status = 'processing' and claim_token is not null and lease_expires_at is not null)
      or (status <> 'processing' and claim_token is null and lease_expires_at is null)
    ),
  constraint ai_generation_requests_metadata_length_check
    check (
      char_length(coalesce(provider, '')) <= 50
      and char_length(coalesce(model, '')) <= 100
      and char_length(coalesce(prompt_version, '')) <= 50
      and char_length(coalesce(finish_reason, '')) <= 50
      and char_length(coalesce(failure_category, '')) <= 64
    ),
  constraint ai_generation_requests_usage_check
    check (
      coalesce(input_tokens, 0) >= 0
      and coalesce(output_tokens, 0) >= 0
      and coalesce(provider_call_count, 0) >= 0
      and coalesce(provider_call_count, 0) <= 20
    )
);

create unique index ai_generation_requests_claim_key
  on private.ai_generation_requests (claim_token)
  where claim_token is not null;

create index ai_generation_requests_cleanup_idx
  on private.ai_generation_requests (updated_at)
  where status in ('completed', 'failed', 'unknown');

alter table private.ai_generation_requests enable row level security;
revoke all on table private.ai_generation_requests from public, anon, authenticated;
grant select, insert, update, delete on table private.ai_generation_requests to service_role;

create trigger ai_generation_requests_set_updated_at
before update on private.ai_generation_requests
for each row execute function private.set_updated_at();

create or replace function public.claim_ai_generation(
  p_user_id uuid,
  p_operation text,
  p_idempotency_key uuid,
  p_request_hash text,
  p_claim_token uuid,
  p_lease_seconds integer
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $function$
declare
  v_request private.ai_generation_requests%rowtype;
begin
  if p_user_id is null or p_operation <> 'meeting_generation'
    or p_idempotency_key is null or p_claim_token is null
    or coalesce(p_request_hash, '') !~ '^[0-9a-f]{64}$'
    or p_lease_seconds not between 60 and 900 then
    raise exception 'Invalid generation claim input.' using errcode = '22023';
  end if;

  insert into private.ai_generation_requests (
    user_id, operation, idempotency_key, request_hash, status,
    claim_token, lease_expires_at
  ) values (
    p_user_id, p_operation, p_idempotency_key, p_request_hash, 'processing',
    p_claim_token, pg_catalog.now() + pg_catalog.make_interval(secs => p_lease_seconds)
  ) on conflict (user_id, operation, idempotency_key) do nothing;

  select request.* into v_request
  from private.ai_generation_requests as request
  where request.user_id = p_user_id
    and request.operation = p_operation
    and request.idempotency_key = p_idempotency_key
  for update;

  if not found then
    raise exception 'Generation claim could not be created.' using errcode = '55000';
  end if;
  if v_request.request_hash <> p_request_hash then
    return pg_catalog.jsonb_build_object('outcome', 'conflict');
  end if;
  if v_request.status = 'completed' then
    return pg_catalog.jsonb_build_object(
      'outcome', 'completed', 'meeting_id', v_request.meeting_id
    );
  end if;
  if v_request.status in ('failed', 'unknown') then
    return pg_catalog.jsonb_build_object(
      'outcome', v_request.status, 'failure_category', v_request.failure_category
    );
  end if;
  if v_request.claim_token = p_claim_token then
    return pg_catalog.jsonb_build_object('outcome', 'claimed');
  end if;
  if v_request.lease_expires_at > pg_catalog.now() then
    return pg_catalog.jsonb_build_object('outcome', 'processing');
  end if;
  if v_request.provider_started_at is not null then
    update private.ai_generation_requests
    set status = 'unknown', claim_token = null, lease_expires_at = null,
        failure_category = 'worker_lost_after_provider_started'
    where id = v_request.id;
    return pg_catalog.jsonb_build_object('outcome', 'unknown');
  end if;

  update private.ai_generation_requests
  set claim_token = p_claim_token,
      lease_expires_at = pg_catalog.now() + pg_catalog.make_interval(secs => p_lease_seconds),
      failure_category = null
  where id = v_request.id;
  return pg_catalog.jsonb_build_object('outcome', 'claimed');
end;
$function$;

create or replace function public.mark_ai_generation_provider_started(
  p_user_id uuid,
  p_idempotency_key uuid,
  p_claim_token uuid
)
returns boolean
language sql
security definer
set search_path = ''
as $function$
  update private.ai_generation_requests
  set provider_started_at = pg_catalog.now()
  where user_id = p_user_id
    and operation = 'meeting_generation'
    and idempotency_key = p_idempotency_key
    and status = 'processing'
    and claim_token = p_claim_token
    and lease_expires_at > pg_catalog.now()
    and provider_started_at is null
  returning true;
$function$;

create or replace function public.fail_ai_generation(
  p_user_id uuid,
  p_idempotency_key uuid,
  p_claim_token uuid,
  p_failure_category text,
  p_uncertain boolean
)
returns boolean
language plpgsql
security definer
set search_path = ''
as $function$
begin
  if char_length(coalesce(p_failure_category, '')) not between 1 and 64 then
    raise exception 'Invalid generation failure category.' using errcode = '22023';
  end if;
  update private.ai_generation_requests
  set status = case when p_uncertain then 'unknown' else 'failed' end,
      failure_category = p_failure_category,
      claim_token = null,
      lease_expires_at = null
  where user_id = p_user_id
    and operation = 'meeting_generation'
    and idempotency_key = p_idempotency_key
    and status = 'processing'
    and claim_token = p_claim_token;
  return found;
end;
$function$;

create or replace function public.persist_ai_generation(
  p_user_id uuid,
  p_idempotency_key uuid,
  p_claim_token uuid,
  p_source_type text,
  p_source_filename text,
  p_source_text text,
  p_summary_html text,
  p_summary_text text,
  p_email_subject text,
  p_requested_role text,
  p_target_language text,
  p_provider text,
  p_model text,
  p_prompt_version text,
  p_finish_reason text,
  p_actions jsonb,
  p_input_tokens integer,
  p_output_tokens integer,
  p_provider_call_count integer
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $function$
declare
  v_request private.ai_generation_requests%rowtype;
  v_meeting_id uuid;
  v_action jsonb;
  v_action_id uuid;
  v_actions jsonb := coalesce(p_actions, '[]'::jsonb);
  v_public_actions jsonb := '[]'::jsonb;
  v_snapshot jsonb := '[]'::jsonb;
  v_title text;
  v_assignee text;
  v_email text;
  v_evidence text;
  v_start date;
  v_deadline date;
begin
  select request.* into v_request
  from private.ai_generation_requests as request
  where request.user_id = p_user_id
    and request.operation = 'meeting_generation'
    and request.idempotency_key = p_idempotency_key
    and request.status = 'processing'
    and request.claim_token = p_claim_token
    and request.lease_expires_at > pg_catalog.now()
    and request.provider_started_at is not null
  for update;
  if not found then
    raise exception 'Generation claim is not active.' using errcode = '55000';
  end if;

  if p_source_type not in ('text','document','audio','video','microphone','system_audio')
    or nullif(pg_catalog.btrim(coalesce(p_source_text, '')), '') is null
    or char_length(p_source_text) > 500000
    or nullif(pg_catalog.btrim(coalesce(p_summary_text, '')), '') is null
    or char_length(p_summary_text) > 250000
    or char_length(coalesce(p_summary_html, '')) > 250000
    or nullif(pg_catalog.btrim(coalesce(p_email_subject, '')), '') is null
    or char_length(p_email_subject) > 200
    or p_email_subject ~ '[\r\n]'
    or char_length(coalesce(p_source_filename, '')) > 255
    or char_length(coalesce(p_requested_role, '')) > 100
    or char_length(coalesce(p_target_language, '')) > 100
    or nullif(pg_catalog.btrim(coalesce(p_provider, '')), '') is null
    or char_length(p_provider) > 50
    or nullif(pg_catalog.btrim(coalesce(p_model, '')), '') is null
    or char_length(p_model) > 100
    or nullif(pg_catalog.btrim(coalesce(p_prompt_version, '')), '') is null
    or char_length(p_prompt_version) > 50
    or nullif(pg_catalog.btrim(coalesce(p_finish_reason, '')), '') is null
    or char_length(p_finish_reason) > 50
    or pg_catalog.jsonb_typeof(v_actions) <> 'array'
    or pg_catalog.jsonb_array_length(v_actions) > 50
    or coalesce(p_input_tokens, 0) < 0
    or coalesce(p_output_tokens, 0) < 0
    or p_provider_call_count not between 1 and 20 then
    raise exception 'Invalid generated meeting result.' using errcode = '22023';
  end if;

  insert into public.meeting_records (
    user_id, client_request_id, source_type, source_filename, source_text,
    summary_html, summary_text, email_subject, requested_role, target_language,
    ai_provider, ai_model, prompt_version, extracted_actions_snapshot
  ) values (
    p_user_id, p_idempotency_key, p_source_type,
    nullif(pg_catalog.btrim(coalesce(p_source_filename, '')), ''), p_source_text,
    p_summary_html, p_summary_text, p_email_subject,
    nullif(pg_catalog.btrim(coalesce(p_requested_role, '')), ''),
    nullif(pg_catalog.btrim(coalesce(p_target_language, '')), ''),
    p_provider, p_model, p_prompt_version, '[]'::jsonb
  ) returning id into v_meeting_id;

  for v_action in
    select value from pg_catalog.jsonb_array_elements(v_actions)
  loop
    if pg_catalog.jsonb_typeof(v_action) <> 'object'
      or exists (
        select 1 from pg_catalog.jsonb_object_keys(v_action) as key
        where key not in ('title','assignee','assignee_email','start_date','deadline','evidence')
      ) then
      raise exception 'Invalid generated action.' using errcode = '22023';
    end if;
    v_title := nullif(pg_catalog.btrim(coalesce(v_action ->> 'title', '')), '');
    v_assignee := nullif(pg_catalog.btrim(coalesce(v_action ->> 'assignee', '')), '');
    v_email := nullif(pg_catalog.lower(pg_catalog.btrim(coalesce(v_action ->> 'assignee_email', ''))), '');
    v_evidence := nullif(pg_catalog.btrim(coalesce(v_action ->> 'evidence', '')), '');
    begin
      v_start := case when nullif(v_action ->> 'start_date', '') is null then null
        else (v_action ->> 'start_date')::date end;
      v_deadline := case when nullif(v_action ->> 'deadline', '') is null then null
        else (v_action ->> 'deadline')::date end;
    exception when others then
      raise exception 'Invalid generated action date.' using errcode = '22023';
    end;
    if v_title is null or char_length(v_title) > 500
      or char_length(coalesce(v_assignee, '')) > 200
      or char_length(coalesce(v_email, '')) > 320
      or (v_email is not null and v_email !~* '^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$')
      or char_length(coalesce(v_evidence, '')) > 500
      or (v_start is not null and v_deadline is not null and v_deadline < v_start) then
      raise exception 'Invalid generated action.' using errcode = '22023';
    end if;

    insert into public.action_items (
      user_id, meeting_id, title, assignee, assignee_email, status, source,
      start_date, deadline, review_status, reviewed_at, ai_evidence
    ) values (
      p_user_id, v_meeting_id, v_title, coalesce(v_assignee, 'Unassigned'),
      v_email, 'not_started', 'ai_generated', v_start, v_deadline,
      'pending', null, v_evidence
    ) returning id into v_action_id;

    v_public_actions := v_public_actions || pg_catalog.jsonb_build_array(
      pg_catalog.jsonb_build_object(
        'id', v_action_id, 'title', v_title,
        'assignee', coalesce(v_assignee, 'Unassigned'),
        'assignee_email', v_email, 'start_date', v_start,
        'deadline', v_deadline, 'evidence', v_evidence,
        'review_status', 'pending', 'reviewed_at', null
      )
    );
    v_snapshot := v_snapshot || pg_catalog.jsonb_build_array(
      pg_catalog.jsonb_build_object(
        'id', v_action_id, 'title', v_title,
        'assignee', coalesce(v_assignee, 'Unassigned'),
        'assigneeEmail', v_email, 'startDate', v_start,
        'deadline', v_deadline, 'evidence', v_evidence,
        'reviewStatus', 'pending'
      )
    );
  end loop;

  update public.meeting_records
  set extracted_actions_snapshot = v_snapshot
  where id = v_meeting_id and user_id = p_user_id;

  update private.ai_generation_requests
  set status = 'completed', claim_token = null, lease_expires_at = null,
      provider = p_provider, model = p_model, prompt_version = p_prompt_version,
      finish_reason = p_finish_reason, meeting_id = v_meeting_id,
      input_tokens = p_input_tokens, output_tokens = p_output_tokens,
      provider_call_count = p_provider_call_count, failure_category = null,
      completed_at = pg_catalog.now()
  where id = v_request.id;

  return pg_catalog.jsonb_build_object(
    'meeting_id', v_meeting_id,
    'formatted_result', p_summary_html,
    'plain_text_summary', p_summary_text,
    'email_subject', p_email_subject,
    'ai_provider', p_provider,
    'ai_model', p_model,
    'prompt_version', p_prompt_version,
    'finish_reason', p_finish_reason,
    'actions', v_public_actions
  );
end;
$function$;

create or replace function public.get_ai_generation_result(
  p_user_id uuid,
  p_idempotency_key uuid
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $function$
declare
  v_result jsonb;
begin
  select pg_catalog.jsonb_build_object(
    'meeting_id', meeting.id,
    'formatted_result', coalesce(meeting.summary_html, ''),
    'plain_text_summary', meeting.summary_text,
    'email_subject', meeting.email_subject,
    'ai_provider', request.provider,
    'ai_model', request.model,
    'prompt_version', request.prompt_version,
    'finish_reason', request.finish_reason,
    'actions', coalesce((
      select pg_catalog.jsonb_agg(
        pg_catalog.jsonb_build_object(
          'id', action.id, 'title', action.title,
          'assignee', action.assignee, 'assignee_email', action.assignee_email,
          'start_date', action.start_date, 'deadline', action.deadline,
          'evidence', action.ai_evidence, 'review_status', action.review_status,
          'reviewed_at', action.reviewed_at
        ) order by action.created_at, action.id
      ) from public.action_items as action
      where action.meeting_id = meeting.id and action.user_id = p_user_id
    ), '[]'::jsonb)
  ) into v_result
  from private.ai_generation_requests as request
  join public.meeting_records as meeting
    on meeting.id = request.meeting_id and meeting.user_id = request.user_id
  where request.user_id = p_user_id
    and request.operation = 'meeting_generation'
    and request.idempotency_key = p_idempotency_key
    and request.status = 'completed';
  return v_result;
end;
$function$;

create or replace function public.review_ai_action(
  p_user_id uuid,
  p_action_item_id uuid,
  p_decision text,
  p_title text,
  p_assignee text,
  p_assignee_email text,
  p_start_date date,
  p_deadline date
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $function$
declare
  v_action public.action_items%rowtype;
  v_title text := nullif(pg_catalog.btrim(coalesce(p_title, '')), '');
  v_assignee text := coalesce(nullif(pg_catalog.btrim(coalesce(p_assignee, '')), ''), 'Unassigned');
  v_email text := nullif(pg_catalog.lower(pg_catalog.btrim(coalesce(p_assignee_email, ''))), '');
begin
  if p_user_id is null or p_action_item_id is null
    or p_decision not in ('confirmed', 'rejected')
    or v_title is null or char_length(v_title) > 500
    or char_length(v_assignee) > 200
    or char_length(coalesce(v_email, '')) > 320
    or (v_email is not null and v_email !~* '^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$')
    or (p_start_date is not null and p_deadline is not null and p_deadline < p_start_date) then
    raise exception 'Invalid action review input.' using errcode = '22023';
  end if;

  select action.* into v_action
  from public.action_items as action
  where action.id = p_action_item_id
    and action.user_id = p_user_id
    and action.source = 'ai_generated'
  for update;
  if not found then
    return pg_catalog.jsonb_build_object('outcome', 'not_found');
  end if;

  -- The material update runs through the invalidation trigger first. The
  -- review decision is then applied in a separate statement in the same
  -- transaction, so a browser cannot combine an edit with forged approval.
  update public.action_items
  set title = v_title,
      assignee = v_assignee,
      assignee_email = v_email,
      start_date = p_start_date,
      deadline = p_deadline
  where id = p_action_item_id and user_id = p_user_id;

  update public.action_items
  set review_status = p_decision,
      reviewed_at = pg_catalog.now()
  where id = p_action_item_id and user_id = p_user_id
  returning * into v_action;

  return pg_catalog.jsonb_build_object(
    'outcome', 'reviewed',
    'action', pg_catalog.jsonb_build_object(
      'id', v_action.id, 'meeting_id', v_action.meeting_id,
      'title', v_action.title, 'assignee', v_action.assignee,
      'assignee_email', v_action.assignee_email, 'status', v_action.status,
      'source', v_action.source, 'start_date', v_action.start_date,
      'deadline', v_action.deadline, 'evidence', v_action.ai_evidence,
      'review_status', v_action.review_status, 'reviewed_at', v_action.reviewed_at,
      'created_at', v_action.created_at
    )
  );
end;
$function$;

revoke all on function public.claim_ai_generation(uuid,text,uuid,text,uuid,integer)
  from public, anon, authenticated;
revoke all on function public.mark_ai_generation_provider_started(uuid,uuid,uuid)
  from public, anon, authenticated;
revoke all on function public.fail_ai_generation(uuid,uuid,uuid,text,boolean)
  from public, anon, authenticated;
revoke all on function public.persist_ai_generation(
  uuid,uuid,uuid,text,text,text,text,text,text,text,text,text,text,text,text,jsonb,integer,integer,integer
) from public, anon, authenticated;
revoke all on function public.get_ai_generation_result(uuid,uuid)
  from public, anon, authenticated;
revoke all on function public.review_ai_action(uuid,uuid,text,text,text,text,date,date)
  from public, anon, authenticated;

grant execute on function public.claim_ai_generation(uuid,text,uuid,text,uuid,integer)
  to service_role;
grant execute on function public.mark_ai_generation_provider_started(uuid,uuid,uuid)
  to service_role;
grant execute on function public.fail_ai_generation(uuid,uuid,uuid,text,boolean)
  to service_role;
grant execute on function public.persist_ai_generation(
  uuid,uuid,uuid,text,text,text,text,text,text,text,text,text,text,text,text,jsonb,integer,integer,integer
) to service_role;
grant execute on function public.get_ai_generation_result(uuid,uuid)
  to service_role;
grant execute on function public.review_ai_action(uuid,uuid,text,text,text,text,date,date)
  to service_role;

comment on table private.ai_generation_requests is
  'Private durable idempotency records. Retain completed rows for 90 days and failed/unknown rows for 30 days after operational review.';

commit;
