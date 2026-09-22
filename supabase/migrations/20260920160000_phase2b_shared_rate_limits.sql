begin;

create table if not exists private.api_rate_limits (
  user_id uuid not null references auth.users (id) on delete cascade,
  route_class text not null,
  window_start timestamptz not null,
  request_count integer not null default 0,
  expires_at timestamptz not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (user_id, route_class, window_start),
  constraint api_rate_limits_route_class_check
    check (route_class ~ '^[a-z][a-z0-9_]{0,63}$'),
  constraint api_rate_limits_request_count_check
    check (request_count >= 0)
);

alter table private.api_rate_limits enable row level security;
alter table private.api_rate_limits force row level security;

revoke all on table private.api_rate_limits from public, anon, authenticated;
grant select, insert, update, delete on table private.api_rate_limits to service_role;

create index if not exists api_rate_limits_expires_at_idx
  on private.api_rate_limits (expires_at);

create or replace function public.consume_rate_limit(
  p_user_id uuid,
  p_route_class text,
  p_limit integer,
  p_window_seconds integer
)
returns table (
  allowed boolean,
  remaining integer,
  retry_after_seconds integer
)
language plpgsql
security definer
set search_path = ''
as $function$
declare
  v_now timestamptz := pg_catalog.clock_timestamp();
  v_window_start timestamptz;
  v_expires_at timestamptz;
  v_count integer;
begin
  if p_user_id is null
    or p_route_class !~ '^[a-z][a-z0-9_]{0,63}$'
    or p_limit < 1
    or p_limit > 100000
    or p_window_seconds < 1
    or p_window_seconds > 604800
  then
    raise exception 'Invalid rate-limit parameters';
  end if;

  v_window_start := pg_catalog.to_timestamp(
    pg_catalog.floor(
      extract(epoch from v_now) / p_window_seconds
    ) * p_window_seconds
  );
  v_expires_at := v_window_start + pg_catalog.make_interval(secs => p_window_seconds);

  insert into private.api_rate_limits (
    user_id,
    route_class,
    window_start,
    request_count,
    expires_at,
    updated_at
  )
  values (
    p_user_id,
    p_route_class,
    v_window_start,
    1,
    v_expires_at,
    v_now
  )
  on conflict (user_id, route_class, window_start)
  do update
    set request_count = private.api_rate_limits.request_count + 1,
        expires_at = excluded.expires_at,
        updated_at = excluded.updated_at
    where private.api_rate_limits.request_count < p_limit
  returning request_count into v_count;

  if v_count is null then
    select bucket.request_count
      into v_count
      from private.api_rate_limits as bucket
     where bucket.user_id = p_user_id
       and bucket.route_class = p_route_class
       and bucket.window_start = v_window_start;

    return query
      select
        false,
        0,
        pg_catalog.greatest(
          1,
          pg_catalog.ceil(
            extract(epoch from (v_expires_at - v_now))
          )::integer
        );
    return;
  end if;

  return query
    select
      true,
      pg_catalog.greatest(0, p_limit - v_count),
      pg_catalog.greatest(
        1,
        pg_catalog.ceil(
          extract(epoch from (v_expires_at - v_now))
        )::integer
      );
end;
$function$;

revoke all on function public.consume_rate_limit(uuid, text, integer, integer)
  from public, anon, authenticated;
grant execute on function public.consume_rate_limit(uuid, text, integer, integer)
  to service_role;

create or replace function public.cleanup_rate_limit_buckets(
  p_batch_size integer default 10000
)
returns integer
language plpgsql
security definer
set search_path = ''
as $function$
declare
  v_deleted integer;
begin
  if p_batch_size < 1 or p_batch_size > 50000 then
    raise exception 'Invalid cleanup batch size';
  end if;

  delete from private.api_rate_limits as bucket
   where bucket.ctid in (
     select expired.ctid
       from private.api_rate_limits as expired
      where expired.expires_at < pg_catalog.now()
      order by expired.expires_at
      limit p_batch_size
   );
  get diagnostics v_deleted = row_count;
  return v_deleted;
end;
$function$;

revoke all on function public.cleanup_rate_limit_buckets(integer)
  from public, anon, authenticated;
grant execute on function public.cleanup_rate_limit_buckets(integer)
  to service_role;

commit;
