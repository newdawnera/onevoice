-- Rollback-only verification for the shared Phase 2B rate limiter.
begin;

insert into auth.users (id, email, created_at, updated_at)
values (
  '30000000-0000-4000-8000-00000000000a',
  'phase2b-rate-limit@example.invalid',
  now(),
  now()
);

do $test$
begin
  if not (
    select c.relrowsecurity and c.relforcerowsecurity
      from pg_catalog.pg_class as c
      join pg_catalog.pg_namespace as n on n.oid = c.relnamespace
     where n.nspname = 'private'
       and c.relname = 'api_rate_limits'
  ) then
    raise exception 'Rate-limit table does not enforce RLS';
  end if;

  if pg_catalog.has_table_privilege(
    'authenticated',
    'private.api_rate_limits',
    'select'
  ) then
    raise exception 'Authenticated users can read backend rate-limit buckets';
  end if;

  if pg_catalog.has_function_privilege(
    'authenticated',
    'public.consume_rate_limit(uuid,text,integer,integer)',
    'execute'
  ) then
    raise exception 'Authenticated users can execute the rate limiter';
  end if;
end;
$test$;

set local role service_role;

do $test$
declare
  first_allowed boolean;
  second_allowed boolean;
  third_allowed boolean;
begin
  select allowed into first_allowed
    from public.consume_rate_limit(
      '30000000-0000-4000-8000-00000000000a',
      'phase2b_test',
      2,
      60
    );
  select allowed into second_allowed
    from public.consume_rate_limit(
      '30000000-0000-4000-8000-00000000000a',
      'phase2b_test',
      2,
      60
    );
  select allowed into third_allowed
    from public.consume_rate_limit(
      '30000000-0000-4000-8000-00000000000a',
      'phase2b_test',
      2,
      60
    );

  if not first_allowed or not second_allowed or third_allowed then
    raise exception 'Atomic rate-limit quota behavior is incorrect';
  end if;
end;
$test$;

reset role;
rollback;
