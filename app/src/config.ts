/** Where the published screen lives, and how it is labelled. */

/**
 * Base URL of the static site written by `mcfinex publish`.
 *
 * Overridable so the app can be pointed at a locally served copy of `site/`
 * during development without editing source. EXPO_PUBLIC_ variables are
 * inlined at build time, which is fine here: this is a public URL, not a
 * secret, and the whole point of the design is that there is nothing to
 * authenticate against.
 */
export const DATA_URL = (
  process.env.EXPO_PUBLIC_MCFINEX_URL ?? 'https://d4t4r.github.io/MCFinEx'
).replace(/\/+$/, '');

export const indexUrl = () => `${DATA_URL}/index.json`;
export const companyUrl = (id: string) => `${DATA_URL}/company/${id}.json`;

/**
 * How long a cached copy is served before a refresh is attempted.
 *
 * Prices change once a night, so anything shorter is a request that can only
 * return what is already on the device. Stale data is still shown while the
 * refresh runs -- an empty screen is worse than yesterday's close, which is
 * clearly labelled with its date anyway.
 */
export const REFRESH_AFTER_MS = 6 * 60 * 60 * 1000;
