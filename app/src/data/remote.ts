/**
 * Fetching and caching the published screen.
 *
 * Every response is written to device storage and served from there first. The
 * data changes once a night, so a network round-trip on every screen open can
 * only ever return what is already on the phone -- and an app that shows
 * nothing without a connection is useless on a train.
 */

import AsyncStorage from '@react-native-async-storage/async-storage';

import { companyUrl, indexUrl } from '../config';
import { Company, IndexPayload, SUPPORTED_SCHEMA } from '../types';

/**
 * The data is newer than this build knows how to read.
 *
 * The app is sideloaded, so an update cannot be pushed to anyone. An old build
 * has to be able to say "I cannot read this" rather than render a screen with
 * fields silently missing.
 */
export class UnsupportedSchemaError extends Error {
  constructor(public readonly found: number) {
    super(
      `This version reads data format ${SUPPORTED_SCHEMA}, but the server is ` +
        `publishing format ${found}. Install a newer build to see current data.`,
    );
    this.name = 'UnsupportedSchemaError';
  }
}

export interface Loaded<T> {
  data: T;
  /** When this copy was fetched, not when the data was generated. */
  fetchedAt: number;
  fromCache: boolean;
}

interface Envelope<T> {
  fetchedAt: number;
  payload: T;
}

const KEY_PREFIX = 'mcfinex:v1:';

async function readCache<T>(key: string): Promise<Envelope<T> | null> {
  try {
    const raw = await AsyncStorage.getItem(KEY_PREFIX + key);
    return raw ? (JSON.parse(raw) as Envelope<T>) : null;
  } catch {
    // A corrupt or unreadable cache is not worth failing over: the network
    // path still works, and the entry will be overwritten on the next success.
    return null;
  }
}

async function writeCache<T>(key: string, payload: T): Promise<number> {
  const fetchedAt = Date.now();
  try {
    await AsyncStorage.setItem(
      KEY_PREFIX + key,
      JSON.stringify({ fetchedAt, payload } satisfies Envelope<T>),
    );
  } catch {
    // Out of space, most likely. Serving the data we just fetched still works;
    // only the offline copy is lost.
  }
  return fetchedAt;
}

async function fetchJson<T extends { schema?: number }>(url: string): Promise<T> {
  const response = await fetch(url, { headers: { Accept: 'application/json' } });
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText} from ${url}`);
  }
  const payload = (await response.json()) as T;
  if (typeof payload.schema === 'number' && payload.schema > SUPPORTED_SCHEMA) {
    throw new UnsupportedSchemaError(payload.schema);
  }
  return payload;
}

/**
 * Cached copy first, network second.
 *
 * `maxAgeMs` decides whether a cached copy is returned without asking the
 * network at all. Callers that want to revalidate a fresh-enough copy do it
 * themselves, so the decision to spend a request stays with the screen.
 */
async function load<T extends { schema?: number }>(
  key: string,
  url: string,
  maxAgeMs: number,
): Promise<Loaded<T>> {
  const cached = await readCache<T>(key);
  if (cached && Date.now() - cached.fetchedAt < maxAgeMs) {
    return { data: cached.payload, fetchedAt: cached.fetchedAt, fromCache: true };
  }

  try {
    const payload = await fetchJson<T>(url);
    const fetchedAt = await writeCache(key, payload);
    return { data: payload, fetchedAt, fromCache: false };
  } catch (error) {
    // A stale copy beats an error screen, but only for a real network failure.
    // A payload this build cannot read is not something a cache can rescue.
    if (cached && !(error instanceof UnsupportedSchemaError)) {
      return { data: cached.payload, fetchedAt: cached.fetchedAt, fromCache: true };
    }
    throw error;
  }
}

export function loadIndex(maxAgeMs: number): Promise<Loaded<IndexPayload>> {
  return load<IndexPayload>('index', indexUrl(), maxAgeMs);
}

export function loadCompany(id: string, maxAgeMs: number): Promise<Loaded<Company>> {
  return load<Company>(`company:${id}`, companyUrl(id), maxAgeMs);
}

/** Drop every cached payload. Used by the "refresh" action in Settings. */
export async function clearCache(): Promise<void> {
  const keys = await AsyncStorage.getAllKeys();
  const ours = keys.filter((k) => k.startsWith(KEY_PREFIX));
  if (ours.length) await AsyncStorage.multiRemove(ours);
}
