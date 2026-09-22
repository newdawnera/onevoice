begin;

-- Phase 2C narrows the broad foundation grants to the operations the current
-- browser UI actually exposes. RLS remains enabled as the row-level boundary;
-- column grants prevent ownership, provenance, linkage, and audit fields from
-- being changed even on a row the caller owns.
alter table public.profiles enable row level security;
alter table public.meeting_records enable row level security;
alter table public.action_items enable row level security;
alter table public.reminder_deliveries enable row level security;

-- UPDATE/DELETE subscriptions are reconciled by refetching. FULL identity keeps
-- the owner column available to the server-side Realtime filter for old rows.
alter table public.action_items replica identity full;

alter table public.meeting_records
  add column client_request_id uuid;

alter table public.meeting_records
  add constraint meeting_records_user_client_request_key
  unique (user_id, client_request_id);

alter table public.meeting_records
  add constraint meeting_records_source_type_check
    check (
      source_type in (
        'text',
        'document',
        'audio',
        'video',
        'microphone',
        'system_audio'
      )
    ),
  add constraint meeting_records_source_filename_length_check
    check (source_filename is null or char_length(source_filename) <= 255),
  add constraint meeting_records_source_html_length_check
    check (source_html is null or char_length(source_html) <= 500000),
  add constraint meeting_records_source_text_length_check
    check (
      source_text is not null
      and char_length(btrim(source_text)) between 1 and 500000
    ),
  add constraint meeting_records_summary_html_length_check
    check (summary_html is null or char_length(summary_html) <= 250000),
  add constraint meeting_records_summary_text_length_check
    check (
      summary_text is not null
      and char_length(btrim(summary_text)) between 1 and 250000
    ),
  add constraint meeting_records_email_subject_length_check
    check (email_subject is null or char_length(email_subject) <= 200),
  add constraint meeting_records_requested_role_length_check
    check (requested_role is null or char_length(requested_role) <= 100),
  add constraint meeting_records_target_language_length_check
    check (target_language is null or char_length(target_language) <= 100),
  add constraint meeting_records_ai_provider_length_check
    check (ai_provider is null or char_length(ai_provider) <= 50),
  add constraint meeting_records_ai_model_length_check
    check (ai_model is null or char_length(ai_model) <= 100),
  add constraint meeting_records_actions_snapshot_count_check
    check (jsonb_array_length(extracted_actions_snapshot) <= 100);

alter table public.action_items
  add constraint action_items_title_content_check
    check (char_length(btrim(title)) between 1 and 500),
  add constraint action_items_assignee_length_check
    check (char_length(assignee) between 1 and 200),
  add constraint action_items_assignee_email_check
    check (
      assignee_email is null
      or (
        char_length(assignee_email) <= 320
        and assignee_email ~* '^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$'
      )
    );

alter table public.profiles
  add constraint profiles_display_name_length_check
    check (
      display_name is null
      or char_length(btrim(display_name)) between 1 and 100
    );

-- Meeting rows are immutable through the browser in this phase.
drop policy if exists meeting_records_insert_own on public.meeting_records;
drop policy if exists meeting_records_update_own on public.meeting_records;
drop policy if exists meeting_records_delete_own on public.meeting_records;

-- Recreate action policies with explicit provenance/linkage requirements.
drop policy if exists action_items_insert_own on public.action_items;
drop policy if exists action_items_update_own on public.action_items;

create policy action_items_insert_manual_own
  on public.action_items
  for insert
  to authenticated
  with check (
    (select auth.uid()) is not null
    and action_items.user_id = (select auth.uid())
    and action_items.meeting_id is null
    and action_items.source = 'manual'
    and action_items.status = 'not_started'
  );

create policy action_items_update_editable_own
  on public.action_items
  for update
  to authenticated
  using (
    (select auth.uid()) is not null
    and action_items.user_id = (select auth.uid())
  )
  with check (
    (select auth.uid()) is not null
    and action_items.user_id = (select auth.uid())
  );

revoke all on table public.profiles from anon, authenticated;
revoke all on table public.meeting_records from anon, authenticated;
revoke all on table public.action_items from anon, authenticated;
revoke all on table public.reminder_deliveries from anon, authenticated;

grant select on table public.profiles to authenticated;
grant update (display_name) on table public.profiles to authenticated;

grant select on table public.meeting_records to authenticated;

grant select, delete on table public.action_items to authenticated;
grant insert (
  user_id,
  title,
  assignee,
  assignee_email,
  start_date,
  deadline
) on table public.action_items to authenticated;
grant update (
  title,
  assignee,
  assignee_email,
  status,
  start_date,
  deadline
) on table public.action_items to authenticated;

-- This trigger is a second boundary behind the column grants. It also protects
-- these invariants if a future privileged caller updates an action row.
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
    or new.created_at is distinct from old.created_at then
    raise exception 'Immutable action-item fields cannot be changed.'
      using errcode = '42501';
  end if;
  return new;
end;
$function$;

revoke all on function private.enforce_action_item_immutability()
from public, anon, authenticated;

create trigger action_items_enforce_immutability
before update on public.action_items
for each row execute function private.enforce_action_item_immutability();

-- SECURITY DEFINER is required here because authenticated clients have no
-- direct meeting INSERT grant and cannot create ai_generated action rows. The
-- function is deliberately narrow, derives the owner from auth.uid(), validates
-- every input, and qualifies every referenced object under an empty search path.
create or replace function public.save_meeting_with_actions(
  p_client_request_id uuid,
  p_source_type text,
  p_source_filename text,
  p_source_html text,
  p_source_text text,
  p_summary_html text,
  p_summary_text text,
  p_email_subject text,
  p_requested_role text,
  p_target_language text,
  p_ai_provider text,
  p_ai_model text,
  p_actions jsonb
)
returns uuid
language plpgsql
security definer
set search_path = ''
as $function$
declare
  v_user_id uuid := auth.uid();
  v_source_type text := nullif(pg_catalog.btrim(coalesce(p_source_type, '')), '');
  v_actions jsonb := coalesce(p_actions, '[]'::jsonb);
  v_snapshot jsonb := '[]'::jsonb;
  v_action jsonb;
  v_title text;
  v_assignee text;
  v_assignee_email text;
  v_start_raw text;
  v_deadline_raw text;
  v_start_date date;
  v_deadline date;
  v_meeting_id uuid;
begin
  if v_user_id is null then
    raise exception 'Authentication is required.' using errcode = '42501';
  end if;

  if p_client_request_id is null then
    raise exception 'A client request ID is required.' using errcode = '22023';
  end if;

  -- Return only a record owned by this caller. The same request UUID can be
  -- used independently by another user without disclosing either row.
  select meeting.id
  into v_meeting_id
  from public.meeting_records as meeting
  where meeting.user_id = v_user_id
    and meeting.client_request_id = p_client_request_id;

  if v_meeting_id is not null then
    return v_meeting_id;
  end if;

  if v_source_type is null or v_source_type not in (
    'text', 'document', 'audio', 'video', 'microphone', 'system_audio'
  ) then
    raise exception 'The source type is invalid.' using errcode = '22023';
  end if;

  if p_source_filename is not null
    and char_length(p_source_filename) > 255 then
    raise exception 'The source filename is too long.' using errcode = '22023';
  end if;
  if p_source_html is not null and char_length(p_source_html) > 500000 then
    raise exception 'The source HTML is too large.' using errcode = '22023';
  end if;
  if nullif(pg_catalog.btrim(coalesce(p_source_text, '')), '') is null
    or char_length(p_source_text) > 500000 then
    raise exception 'The source text is invalid.' using errcode = '22023';
  end if;
  if p_summary_html is not null and char_length(p_summary_html) > 250000 then
    raise exception 'The summary HTML is too large.' using errcode = '22023';
  end if;
  if nullif(pg_catalog.btrim(coalesce(p_summary_text, '')), '') is null
    or char_length(p_summary_text) > 250000 then
    raise exception 'The summary text is invalid.' using errcode = '22023';
  end if;
  if p_email_subject is not null and char_length(p_email_subject) > 200 then
    raise exception 'The email subject is too long.' using errcode = '22023';
  end if;
  if p_requested_role is not null and char_length(p_requested_role) > 100 then
    raise exception 'The requested role is too long.' using errcode = '22023';
  end if;
  if p_target_language is not null and char_length(p_target_language) > 100 then
    raise exception 'The target language is too long.' using errcode = '22023';
  end if;
  if p_ai_provider is not null and char_length(p_ai_provider) > 50 then
    raise exception 'The AI provider value is too long.' using errcode = '22023';
  end if;
  if p_ai_model is not null and char_length(p_ai_model) > 100 then
    raise exception 'The AI model value is too long.' using errcode = '22023';
  end if;

  if pg_catalog.jsonb_typeof(v_actions) <> 'array' then
    raise exception 'Actions must be a JSON array.' using errcode = '22023';
  end if;
  if pg_catalog.jsonb_array_length(v_actions) > 100 then
    raise exception 'Too many actions were supplied.' using errcode = '22023';
  end if;

  for v_action in
    select action.value
    from pg_catalog.jsonb_array_elements(v_actions) as action(value)
  loop
    if pg_catalog.jsonb_typeof(v_action) <> 'object' then
      raise exception 'Every action must be a JSON object.' using errcode = '22023';
    end if;

    if (v_action - array[
      'title', 'task', 'assignee', 'assigneeEmail', 'startDate', 'deadline'
    ]::text[]) <> '{}'::jsonb then
      raise exception 'An action contains unsupported fields.' using errcode = '22023';
    end if;

    v_title := nullif(
      pg_catalog.btrim(coalesce(v_action ->> 'title', v_action ->> 'task', '')),
      ''
    );
    if v_title is null or char_length(v_title) > 500 then
      raise exception 'An action title is invalid.' using errcode = '22023';
    end if;

    v_assignee := coalesce(
      nullif(pg_catalog.btrim(coalesce(v_action ->> 'assignee', '')), ''),
      'Unassigned'
    );
    if char_length(v_assignee) > 200 then
      raise exception 'An action assignee is too long.' using errcode = '22023';
    end if;

    v_assignee_email := nullif(
      pg_catalog.btrim(coalesce(v_action ->> 'assigneeEmail', '')),
      ''
    );
    if v_assignee_email is not null and (
      char_length(v_assignee_email) > 320
      or v_assignee_email !~* '^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$'
    ) then
      raise exception 'An action email is invalid.' using errcode = '22023';
    end if;

    v_start_raw := nullif(pg_catalog.btrim(coalesce(v_action ->> 'startDate', '')), '');
    v_deadline_raw := nullif(pg_catalog.btrim(coalesce(v_action ->> 'deadline', '')), '');
    if v_start_raw is not null and v_start_raw !~ '^\d{4}-\d{2}-\d{2}$' then
      raise exception 'An action start date is invalid.' using errcode = '22007';
    end if;
    if v_deadline_raw is not null and v_deadline_raw !~ '^\d{4}-\d{2}-\d{2}$' then
      raise exception 'An action deadline is invalid.' using errcode = '22007';
    end if;

    v_start_date := case when v_start_raw is null then null else v_start_raw::date end;
    v_deadline := case when v_deadline_raw is null then null else v_deadline_raw::date end;
    if v_start_date is not null
      and v_deadline is not null
      and v_deadline < v_start_date then
      raise exception 'An action deadline precedes its start date.' using errcode = '22023';
    end if;

    v_snapshot := v_snapshot || pg_catalog.jsonb_build_array(
      pg_catalog.jsonb_build_object(
        'title', v_title,
        'assignee', v_assignee,
        'assigneeEmail', v_assignee_email,
        'startDate', v_start_raw,
        'deadline', v_deadline_raw
      )
    );
  end loop;

  insert into public.meeting_records (
    user_id,
    client_request_id,
    source_type,
    source_filename,
    source_html,
    source_text,
    summary_html,
    summary_text,
    email_subject,
    requested_role,
    target_language,
    ai_provider,
    ai_model,
    extracted_actions_snapshot
  )
  values (
    v_user_id,
    p_client_request_id,
    v_source_type,
    nullif(pg_catalog.btrim(coalesce(p_source_filename, '')), ''),
    p_source_html,
    p_source_text,
    p_summary_html,
    p_summary_text,
    nullif(pg_catalog.btrim(coalesce(p_email_subject, '')), ''),
    nullif(pg_catalog.btrim(coalesce(p_requested_role, '')), ''),
    nullif(pg_catalog.btrim(coalesce(p_target_language, '')), ''),
    nullif(pg_catalog.btrim(coalesce(p_ai_provider, '')), ''),
    nullif(pg_catalog.btrim(coalesce(p_ai_model, '')), ''),
    v_snapshot
  )
  on conflict (user_id, client_request_id) do nothing
  returning id into v_meeting_id;

  if v_meeting_id is null then
    select meeting.id
    into v_meeting_id
    from public.meeting_records as meeting
    where meeting.user_id = v_user_id
      and meeting.client_request_id = p_client_request_id;
    if v_meeting_id is null then
      raise exception 'The meeting could not be saved.' using errcode = '40001';
    end if;
    return v_meeting_id;
  end if;

  insert into public.action_items (
    user_id,
    meeting_id,
    title,
    assignee,
    assignee_email,
    status,
    source,
    start_date,
    deadline
  )
  select
    v_user_id,
    v_meeting_id,
    action.value ->> 'title',
    action.value ->> 'assignee',
    nullif(action.value ->> 'assigneeEmail', ''),
    'not_started',
    'ai_generated',
    nullif(action.value ->> 'startDate', '')::date,
    nullif(action.value ->> 'deadline', '')::date
  from pg_catalog.jsonb_array_elements(v_snapshot) as action(value);

  return v_meeting_id;
end;
$function$;

comment on function public.save_meeting_with_actions(
  uuid, text, text, text, text, text, text, text, text, text, text, text, jsonb
) is
  'Atomically saves one authenticated user meeting and its normalized AI actions. SECURITY DEFINER is required because direct browser meeting and AI-action inserts are revoked.';

revoke all on function public.save_meeting_with_actions(
  uuid, text, text, text, text, text, text, text, text, text, text, text, jsonb
) from public, anon, authenticated;
grant execute on function public.save_meeting_with_actions(
  uuid, text, text, text, text, text, text, text, text, text, text, text, jsonb
) to authenticated;

-- Postgres Changes is used only for these two owner-filtered application tables.
-- The guards make the migration safe if a dashboard toggle added either table.
do $publication$
begin
  if not exists (
    select 1
    from pg_catalog.pg_publication
    where pubname = 'supabase_realtime'
  ) then
    execute 'create publication supabase_realtime';
  end if;

  if not exists (
    select 1
    from pg_catalog.pg_publication_tables
    where pubname = 'supabase_realtime'
      and schemaname = 'public'
      and tablename = 'meeting_records'
  ) then
    execute 'alter publication supabase_realtime add table public.meeting_records';
  end if;

  if not exists (
    select 1
    from pg_catalog.pg_publication_tables
    where pubname = 'supabase_realtime'
      and schemaname = 'public'
      and tablename = 'action_items'
  ) then
    execute 'alter publication supabase_realtime add table public.action_items';
  end if;
end;
$publication$;

commit;
