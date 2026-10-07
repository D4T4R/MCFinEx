-- Per-reader state for the web screener. Run once in the Supabase SQL editor.
--
-- This file is the entire protection on these tables. The browser holds the anon
-- key, which is public by design -- it identifies the project, it grants nothing
-- -- so what a signed-in reader can touch is decided here and nowhere else. Get a
-- policy wrong and any account can read every other account's rows, with no
-- symptom until somebody looks.
--
-- Deliberately separate from the screening schema in src/mcfinex/db/schema.sql.
-- That one is created and migrated by the pipeline, which runs as the owner and
-- has no business holding user data; this one is reached only from a browser.
-- Keeping them apart means `mcfinex init` can never drop a reader's watchlist.

create table if not exists public.watchlist (
    user_id    uuid        not null references auth.users (id) on delete cascade,
    company_id text        not null,
    created_at timestamptz not null default now(),
    primary key (user_id, company_id)
);

-- Saved filter sets, so a reader does not rebuild the same screen every visit.
-- `filters` is jsonb rather than columns: the screener's filters change with the
-- UI, and a migration per control is a tax on changing the front end.
create table if not exists public.saved_screen (
    id         uuid        primary key default gen_random_uuid(),
    user_id    uuid        not null references auth.users (id) on delete cascade,
    name       text        not null,
    filters    jsonb       not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    unique (user_id, name)
);

alter table public.watchlist     enable row level security;
alter table public.saved_screen  enable row level security;

-- One policy per operation rather than one `for all`, so a mistake in the write
-- rule cannot silently widen reads. `auth.uid()` is the signed-in user; it is
-- null for an anonymous request, and null = user_id is never true, so an
-- unauthenticated caller matches no row rather than all of them.
--
-- `with check` as well as `using` on insert and update: `using` decides which
-- rows may be touched, `with check` decides what they may be changed *to*.
-- Without the latter a reader could move a row onto somebody else's user_id.

drop policy if exists watchlist_select on public.watchlist;
create policy watchlist_select on public.watchlist
    for select using (auth.uid() = user_id);

drop policy if exists watchlist_insert on public.watchlist;
create policy watchlist_insert on public.watchlist
    for insert with check (auth.uid() = user_id);

drop policy if exists watchlist_delete on public.watchlist;
create policy watchlist_delete on public.watchlist
    for delete using (auth.uid() = user_id);

drop policy if exists saved_screen_select on public.saved_screen;
create policy saved_screen_select on public.saved_screen
    for select using (auth.uid() = user_id);

drop policy if exists saved_screen_insert on public.saved_screen;
create policy saved_screen_insert on public.saved_screen
    for insert with check (auth.uid() = user_id);

drop policy if exists saved_screen_update on public.saved_screen;
create policy saved_screen_update on public.saved_screen
    for update using (auth.uid() = user_id) with check (auth.uid() = user_id);

drop policy if exists saved_screen_delete on public.saved_screen;
create policy saved_screen_delete on public.saved_screen
    for delete using (auth.uid() = user_id);

-- Check it took. With RLS on and no policy matching, a select returns zero rows
-- rather than an error -- so "it works" and "it is wide open" look identical from
-- the client, and only the catalogue can tell you which you have.
--
--   select relname, relrowsecurity from pg_class
--    where relname in ('watchlist', 'saved_screen');          -- both must be true
--
--   select tablename, policyname, cmd, qual, with_check
--     from pg_policies where schemaname = 'public'
--    order by tablename, cmd;                                 -- seven policies
