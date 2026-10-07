/**
 * Fetching the published screen.
 *
 * Two files, both static and both already on a CDN, so there is no cache to
 * manage beyond the browser's own: `screen.json` once on load, and
 * `company/{id}.json` when somebody opens a company.
 *
 * Unlike the phone app there is no offline story to keep. A page that cannot
 * reach the data has nothing to show and should say so, rather than render a
 * shell that looks like an empty market.
 */

import { SUPPORTED_SCHEMA, type CompanyDetail, type ScreenPayload } from '../types';

/** Where the nightly job publishes. Overridable for local work. */
export const BASE_URL = (
  import.meta.env.VITE_MCFINEX_URL ?? 'https://d4t4r.github.io/MCFinEx'
).replace(/\/+$/, '');

export class UnsupportedSchemaError extends Error {
  constructor(readonly got: number) {
    super(
      `This page understands payload schema ${SUPPORTED_SCHEMA} but the data ` +
        `says ${got}. Reload to pick up the current build.`,
    );
    this.name = 'UnsupportedSchemaError';
  }
}

async function getJson<T extends { schema: number }>(path: string): Promise<T> {
  const response = await fetch(`${BASE_URL}/${path}`, {
    headers: { Accept: 'application/json' },
  });
  if (!response.ok) {
    throw new Error(`${path}: ${response.status} ${response.statusText}`);
  }
  const payload = (await response.json()) as T;
  // Checked before anything reads a field. A newer payload may have changed what
  // a name means, and rendering it under the old reading is worse than refusing.
  if (payload.schema > SUPPORTED_SCHEMA) {
    throw new UnsupportedSchemaError(payload.schema);
  }
  return payload;
}

export function loadScreen(): Promise<ScreenPayload> {
  return getJson<ScreenPayload>('screen.json');
}

export function loadCompany(id: string): Promise<CompanyDetail> {
  // `id` comes from the payload itself, never from a user, so it needs no
  // escaping -- but it is encoded anyway, because that assumption is one
  // refactor away from being false.
  return getJson<CompanyDetail>(`company/${encodeURIComponent(id)}.json`);
}
