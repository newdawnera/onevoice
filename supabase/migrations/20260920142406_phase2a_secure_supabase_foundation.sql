begin;

-- Keep privileged trigger helpers outside the Data API's exposed schemas.
create schema if not exists private;
revoke all on schema private from public;
revoke all on schema private from anon, authenticated;

create table if not exists public.profiles (
  id uuid primary key references auth.users (id) on delete cascade,
  display_name text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.meeting_records (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  source_type text not null default 'text',
  source_filename text,
  source_html text,
  source_text text,
  summary_html text,
  summary_text text,
  email_subject text,
  requested_role text,
  target_language text,
  ai_provider text,
  ai_model text,
  extracted_actions_snapshot jsonb not null default '[]'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint meeting_records_actions_snapshot_array_check
    check (jsonb_typeof(extracted_actions_snapshot) = 'array'),
  constraint meeting_records_id_user_id_key unique (id, user_id)
);

create table if not exists public.action_items (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  meeting_id uuid,
  title text not null,
  assignee text not null default 'Unassigned',
  assignee_email text,
  status text not null default 'not_started',
  source text not null default 'manual',
  start_date date,
  deadline date,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint action_items_status_check
    check (status in ('not_started', 'in_progress', 'completed')),
  constraint action_items_source_check
    check (source in ('manual', 'ai_generated')),
  constraint action_items_date_order_check
    check (start_date is null or deadline is null or deadline >= start_date),
  constraint action_items_meeting_owner_fkey
    foreign key (meeting_id, user_id)
    references public.meeting_records (id, user_id)
    on delete set null (meeting_id)
);

create table if not exists public.reminder_deliveries (
  id uuid primary key default gen_random_uuid(),
  action_item_id uuid not null references public.action_items (id) on delete cascade,
  reminder_type text not null,
  scheduled_for date,
  dedupe_key text,
  status text not null default 'pending',
  provider_message_id text,
  error_message text,
  created_at timestamptz not null default now(),
  sent_at timestamptz,
  constraint reminder_deliveries_dedupe_key_key unique (dedupe_key),
  constraint reminder_deliveries_status_check
    check (status in ('pending', 'sent', 'failed')),
  constraint reminder_deliveries_type_check
    check (
      reminder_type in (
        'start_date',
        'before_deadline',
        'deadline',
        'manual_notification'
      )
    )
);

comment on column public.reminder_deliveries.dedupe_key is
  'Optional idempotency key. NULL is allowed so repeated manual reminders on the same date are not blocked.';

create or replace function private.set_updated_at()
returns trigger
language plpgsql
set search_path = ''
as $function$
begin
  new.updated_at := pg_catalog.now();
  return new;
end;
$function$;

revoke all on function private.set_updated_at() from public, anon, authenticated;

create or replace function private.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = ''
as $function$
begin
  insert into public.profiles (id, display_name)
  values (
    new.id,
    coalesce(
      nullif(pg_catalog.btrim(new.raw_user_meta_data ->> 'display_name'), ''),
      nullif(pg_catalog.btrim(new.raw_user_meta_data ->> 'username'), ''),
      nullif(
        pg_catalog.split_part(coalesce(new.email, ''), '@', 1),
        ''
      ),
      'User'
    )
  )
  on conflict (id) do nothing;

  return new;
end;
$function$;

revoke all on function private.handle_new_user() from public, anon, authenticated;

create or replace trigger profiles_set_updated_at
before update on public.profiles
for each row execute function private.set_updated_at();

create or replace trigger meeting_records_set_updated_at
before update on public.meeting_records
for each row execute function private.set_updated_at();

create or replace trigger action_items_set_updated_at
before update on public.action_items
for each row execute function private.set_updated_at();

create or replace trigger on_auth_user_created
after insert on auth.users
for each row execute function private.handle_new_user();

create index if not exists meeting_records_user_created_idx
  on public.meeting_records (user_id, created_at desc);

create index if not exists action_items_user_created_idx
  on public.action_items (user_id, created_at desc);

create index if not exists action_items_user_status_idx
  on public.action_items (user_id, status);

create index if not exists action_items_meeting_id_idx
  on public.action_items (meeting_id)
  where meeting_id is not null;

create index if not exists action_items_incomplete_start_date_idx
  on public.action_items (start_date, user_id)
  where status <> 'completed' and start_date is not null;

create index if not exists action_items_incomplete_deadline_idx
  on public.action_items (deadline, user_id)
  where status <> 'completed' and deadline is not null;

create index if not exists reminder_deliveries_action_item_idx
  on public.reminder_deliveries (action_item_id);

alter table public.profiles enable row level security;
alter table public.meeting_records enable row level security;
alter table public.action_items enable row level security;
alter table public.reminder_deliveries enable row level security;

-- Explicit grants complement RLS. The project is configured not to auto-grant
-- new public tables to Data API roles.
revoke all on table public.profiles from anon, authenticated;
revoke all on table public.meeting_records from anon, authenticated;
revoke all on table public.action_items from anon, authenticated;
revoke all on table public.reminder_deliveries from anon, authenticated;

grant usage on schema public to authenticated;
grant select, update on table public.profiles to authenticated;
grant select, insert, update, delete on table public.meeting_records to authenticated;
grant select, insert, update, delete on table public.action_items to authenticated;

grant select, insert, update, delete on table public.profiles to service_role;
grant select, insert, update, delete on table public.meeting_records to service_role;
grant select, insert, update, delete on table public.action_items to service_role;
grant select, insert, update, delete on table public.reminder_deliveries to service_role;

do $policy$
begin
  if not exists (
    select 1 from pg_catalog.pg_policies
    where schemaname = 'public'
      and tablename = 'profiles'
      and policyname = 'profiles_select_own'
  ) then
    create policy profiles_select_own
      on public.profiles
      for select
      to authenticated
      using (
        (select auth.uid()) is not null
        and id = (select auth.uid())
      );
  end if;

  if not exists (
    select 1 from pg_catalog.pg_policies
    where schemaname = 'public'
      and tablename = 'profiles'
      and policyname = 'profiles_update_own'
  ) then
    create policy profiles_update_own
      on public.profiles
      for update
      to authenticated
      using (
        (select auth.uid()) is not null
        and id = (select auth.uid())
      )
      with check (
        (select auth.uid()) is not null
        and id = (select auth.uid())
      );
  end if;

  if not exists (
    select 1 from pg_catalog.pg_policies
    where schemaname = 'public'
      and tablename = 'meeting_records'
      and policyname = 'meeting_records_select_own'
  ) then
    create policy meeting_records_select_own
      on public.meeting_records
      for select
      to authenticated
      using (
        (select auth.uid()) is not null
        and meeting_records.user_id = (select auth.uid())
      );
  end if;

  if not exists (
    select 1 from pg_catalog.pg_policies
    where schemaname = 'public'
      and tablename = 'meeting_records'
      and policyname = 'meeting_records_insert_own'
  ) then
    create policy meeting_records_insert_own
      on public.meeting_records
      for insert
      to authenticated
      with check (
        (select auth.uid()) is not null
        and meeting_records.user_id = (select auth.uid())
      );
  end if;

  if not exists (
    select 1 from pg_catalog.pg_policies
    where schemaname = 'public'
      and tablename = 'meeting_records'
      and policyname = 'meeting_records_update_own'
  ) then
    create policy meeting_records_update_own
      on public.meeting_records
      for update
      to authenticated
      using (
        (select auth.uid()) is not null
        and meeting_records.user_id = (select auth.uid())
      )
      with check (
        (select auth.uid()) is not null
        and meeting_records.user_id = (select auth.uid())
      );
  end if;

  if not exists (
    select 1 from pg_catalog.pg_policies
    where schemaname = 'public'
      and tablename = 'meeting_records'
      and policyname = 'meeting_records_delete_own'
  ) then
    create policy meeting_records_delete_own
      on public.meeting_records
      for delete
      to authenticated
      using (
        (select auth.uid()) is not null
        and meeting_records.user_id = (select auth.uid())
      );
  end if;

  if not exists (
    select 1 from pg_catalog.pg_policies
    where schemaname = 'public'
      and tablename = 'action_items'
      and policyname = 'action_items_select_own'
  ) then
    create policy action_items_select_own
      on public.action_items
      for select
      to authenticated
      using (
        (select auth.uid()) is not null
        and action_items.user_id = (select auth.uid())
      );
  end if;

  if not exists (
    select 1 from pg_catalog.pg_policies
    where schemaname = 'public'
      and tablename = 'action_items'
      and policyname = 'action_items_insert_own'
  ) then
    create policy action_items_insert_own
      on public.action_items
      for insert
      to authenticated
      with check (
        (select auth.uid()) is not null
        and action_items.user_id = (select auth.uid())
        and (
          action_items.meeting_id is null
          or exists (
            select 1
            from public.meeting_records as meeting
            where meeting.id = action_items.meeting_id
              and meeting.user_id = (select auth.uid())
          )
        )
      );
  end if;

  if not exists (
    select 1 from pg_catalog.pg_policies
    where schemaname = 'public'
      and tablename = 'action_items'
      and policyname = 'action_items_update_own'
  ) then
    create policy action_items_update_own
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
        and (
          action_items.meeting_id is null
          or exists (
            select 1
            from public.meeting_records as meeting
            where meeting.id = action_items.meeting_id
              and meeting.user_id = (select auth.uid())
          )
        )
      );
  end if;

  if not exists (
    select 1 from pg_catalog.pg_policies
    where schemaname = 'public'
      and tablename = 'action_items'
      and policyname = 'action_items_delete_own'
  ) then
    create policy action_items_delete_own
      on public.action_items
      for delete
      to authenticated
      using (
        (select auth.uid()) is not null
        and action_items.user_id = (select auth.uid())
      );
  end if;
end;
$policy$;

-- No reminder_deliveries policy is intentional. Only the server-side service
-- role receives table privileges in this phase.

commit;
