# MCFinEx on Android

Reads the static JSON published by `mcfinex publish`. There is no server, no
account and no login: the app fetches two kinds of file and caches them on the
device.

```
index.json            the whole browse list, ~99 KB gzipped, fetched once
company/{id}.json     signals and trends, ~1.4 KB, fetched on tap
```

`id` is the ticker with `&` mapped to `_` (`M&M` → `M_M.json`) and travels with
each pick, so the client never reimplements the rule.

## Run it

```bash
npm install
npm start              # then press `a` for Android, `w` for web
npm test               # jest, no device needed
npx tsc --noEmit       # typecheck
```

By default it reads `https://d4t4r.github.io/MCFinEx`. To work against a local
build of the site:

```bash
# from the repository root
mcfinex publish --out site
python scripts/serve_site.py            # CORS + no-store, on :8531
```

and put this in `app/.env.local` (gitignored):

```
EXPO_PUBLIC_MCFINEX_URL=http://127.0.0.1:8531
```

## Notifications

Push is by **FCM topic**, not device token. The phone subscribes itself, so
there is no device registry, no write endpoint and no record of who is running
the app. `mcfinex notify` publishes to the same topic names.

Everything degrades to a no-op when Firebase is absent, so the app runs on web
and in a build with no `google-services.json` — the Alerts screen says so rather
than offering switches that quietly do nothing.

To turn it on:

1. Create a Firebase project and add an Android app with package
   `com.d4t4r.mcfinex`.
2. Download `google-services.json` into `app/`. It is gitignored: it ships
   inside the APK and is not a secret, but this repository is public and the
   file names the project.
3. Give EAS a copy, since it cannot read a gitignored file:
   ```bash
   eas secret:create --scope project --name GOOGLE_SERVICES_JSON \
     --type file --value ./google-services.json
   ```

## Build an APK

```bash
npm install -g eas-cli
eas login
eas init                                  # writes extra.eas.projectId
eas build -p android --profile preview    # APK, internal distribution
```

`preview` and `production` both build an APK rather than an AAB: this is never
going to a store, it is installed by hand.

## Where the payload contract lives

`src/types.ts` mirrors `src/mcfinex/publish.py`. They are in one repository so a
payload change and the code reading it land in the same commit — the app is
sideloaded, so no update can be pushed to anyone holding an old build.

Every payload carries `"schema"`. A build refuses data newer than it understands
and says so, rather than rendering a screen with fields silently missing.

The full disclaimer is compiled in as a fallback (`src/disclaimer.ts`) because
the Alerts screen is reachable before anything has been fetched. It is kept
identical to `mcfinex/disclaimer.py` by `tests/test_disclaimer.py`; regenerate
it with:

```bash
python -c "from mcfinex.disclaimer import FULL; import json; \
  print('export const FULL_DISCLAIMER = ' + json.dumps(FULL) + ';')"
```
