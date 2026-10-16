-- Requests for a company's financials. Run once in the Supabase SQL editor.
--
-- The SME board is priced every night but not scraped, because 454 companies is
-- about two hours of enrichment a night spent on filings nobody has asked to
-- read. A reader who wants one asks for it, and the nightly job collects it.
--
-- Deliberately *not* in src/mcfinex/db/schema.sql. That file is applied to a
-- local SQLite database too, and this table references auth.users and relies on
-- row-level security -- neither of which exists there. The pipeline reads this
-- table when it is present and carries on when it is not.
--
-- The quota is enforced here and nowhere else. A limit checked in the browser is
-- not a limit: it is a disabled button in front of an open endpoint.

create table if not exists public.scrape_request (
    id           uuid        primary key default gen_random_uuid(),
    user_id      uuid        not null references auth.users (id) on delete cascade,
    ticker       text        not null,
    requested_at timestamptz not null default now(),
    -- When the nightly job accepted it and flagged the company for scraping.
    -- Null means still queued. Not "fulfilled": whether the scrape succeeded is
    -- answered by the company having financials, not by a column here.
    promoted_at  timestamptz
);

-- One pending request per person per company. Asking twice for the same thing
-- should not cost two of the week's two.
create unique index if not exists scrape_request_pending
    on public.scrape_request (user_id, ticker)
    where promoted_at is null;

create index if not exists scrape_request_queue
    on public.scrape_request (requested_at)
    where promoted_at is null;

alter table public.scrape_request enable row level security;

-- The quota count runs as the function owner, deliberately.
--
-- A policy on scrape_request cannot sub-query scrape_request: Postgres applies
-- the table's own policies to that sub-query and recurses, which fails at run
-- time with "infinite recursion detected in policy". SECURITY DEFINER steps
-- outside RLS for the count only.
--
-- search_path is pinned. Without it the owner's path is attacker-controllable
-- through a schema earlier in the search order, which is the standard way a
-- SECURITY DEFINER function becomes privilege escalation.
create or replace function public.weekly_request_count(uid uuid)
returns integer
language sql
security definer
stable
set search_path = public, pg_temp
as $$
    select count(*)::int
      from public.scrape_request
     where user_id = uid
       and requested_at > now() - interval '7 days'
$$;

revoke all on function public.weekly_request_count(uuid) from public;
grant execute on function public.weekly_request_count(uuid) to authenticated;

drop policy if exists scrape_request_select on public.scrape_request;
create policy scrape_request_select on public.scrape_request
    for select using (auth.uid() = user_id);

-- Two a week, counted server side.
--
-- `auth.uid() = user_id` first, so a caller cannot spend somebody else's quota
-- or file a request against another account. The count is of *requests made*,
-- not requests pending, so deleting one does not refund it -- otherwise the
-- limit is two concurrent rather than two a week, which is not a limit.
drop policy if exists scrape_request_insert on public.scrape_request;
create policy scrape_request_insert on public.scrape_request
    for insert with check (
        auth.uid() = user_id
        and public.weekly_request_count(auth.uid()) < 2
    );

-- Withdrawing a request is allowed; it does not give the quota back.
drop policy if exists scrape_request_delete on public.scrape_request;
create policy scrape_request_delete on public.scrape_request
    for delete using (auth.uid() = user_id and promoted_at is null);

-- No update policy at all. `promoted_at` is the pipeline's to set, and it
-- connects as the owner rather than through PostgREST, so it is not bound by
-- these policies. A reader who could write that column could mark their own
-- request done, or un-done, forever.

-- Check it took. With RLS on and no matching policy a select returns zero rows
-- rather than an error, so "working" and "wide open" look the same from the
-- client and only the catalogue can tell them apart.
--
--   select relname, relrowsecurity from pg_class where relname = 'scrape_request';
--   select policyname, cmd, qual, with_check from pg_policies
--    where tablename = 'scrape_request' order by cmd;      -- three, no UPDATE
