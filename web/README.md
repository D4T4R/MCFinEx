# MCFinEx on the web

A screener over the static JSON the nightly job publishes. No backend: the page
fetches two files from GitHub Pages and does the rest in the browser.

```
screen.json           every screened company with the fundamentals charts plot
                      on — 2,544 rows, ~240 KB gzipped, fetched once
company/{id}.json     signals and quarterly history, fetched on tap
```

Detail files exist only for companies that reached a shortlist (1,569 of 2,544).
The screener lists all of them regardless — a distribution with the untiered
removed is not the market — so a company without a detail file renders its
summary and says the breakdown is not published. It is a normal state, not a 404.

## Run it

```bash
npm install
npm run dev          # http://localhost:5173
npm test             # vitest
npm run typecheck
npm run build
```

By default it reads `https://d4t4r.github.io/MCFinEx`. To work against a local
build of the site, from the repository root:

```bash
mcfinex publish --out site
python scripts/serve_site.py      # CORS + no-store, on :8531
```

and put this in `web/.env.local` (gitignored):

```
VITE_MCFINEX_URL=http://127.0.0.1:8531
```

## Accounts

Signing in buys a watchlist and saved screens that follow you between devices.
**It does not gate anything.** The screen is published as static JSON on a CDN
and is readable by anyone with the URL, so a login in front of this page would
be a door beside an open window — and pretending otherwise would be worse than
having no login at all.

Supabase, because the pipeline already uses that project. Two env vars:

```
VITE_SUPABASE_URL=https://<ref>.supabase.co
VITE_SUPABASE_ANON_KEY=<anon key>
```

The anon key belongs in the browser — it is a public identifier, not a secret.
What protects the tables is `supabase/preferences.sql`, which must be run once in
the SQL editor. **The service-role key must never appear in this directory**; it
bypasses row-level security entirely.

Without those two variables the sign-in is simply not offered, and the watchlist
lives in `localStorage` only.

## Cloudflare Pages

Connect the repository once, in the dashboard:

| Setting | Value |
|---|---|
| Framework preset | None |
| Build command | `npm run build` |
| Build output directory | `dist` |
| Root directory | `web` |
| Node version | 22 (set `NODE_VERSION=22` under environment variables) |

Then add `VITE_SUPABASE_URL` and `VITE_SUPABASE_ANON_KEY` as environment
variables for both Production and Preview. Vite inlines `VITE_*` at build time,
so a value added after a deploy needs a rebuild to take effect — there is no
runtime config to change.

`public/_redirects` sends every path to `index.html` with a 200, which is what
keeps `/company/ADVANIHOTR` working when somebody opens it directly rather than
clicking through. `public/_headers` sets the CSP; its `connect-src` lists the
published-data origin and Supabase, so changing either means changing that line.

### Supabase redirect URLs

After the first deploy, add the Pages URL to Supabase under Authentication →
URL Configuration → Redirect URLs, including the preview pattern:

```
https://<project>.pages.dev/**
https://<your-domain>/**
```

Sign-in silently fails back to the site root without this, which looks like the
login having been declined.
