/**
 * Fetching, caching and the schema guard.
 *
 * The cache is the reason the app works on a train, and the schema guard is the
 * reason an old sideloaded build says "update me" instead of rendering a screen
 * with fields silently missing.
 */

import AsyncStorage from '@react-native-async-storage/async-storage';

import { clearCache, loadCompany, loadIndex, UnsupportedSchemaError } from '../data/remote';
import { SUPPORTED_SCHEMA } from '../types';

const INDEX = {
  schema: SUPPORTED_SCHEMA,
  generated: '2026-09-05T17:00:00Z',
  price_date: '2026-09-04',
  last_scraped: '2026-08-21',
  universe: 2544,
  tiers: { 'High conviction': 111 },
  sectors: [],
  picks: [],
  disclaimer: 'short',
  disclaimer_full: 'full',
};

function respond(payload: unknown, ok = true, status = 200) {
  return jest.fn().mockResolvedValue({
    ok,
    status,
    statusText: ok ? 'OK' : 'Not Found',
    json: async () => payload,
  });
}

beforeEach(async () => {
  await AsyncStorage.clear();
  jest.restoreAllMocks();
});

describe('loadIndex', () => {
  it('fetches and returns the payload', async () => {
    global.fetch = respond(INDEX) as never;
    const result = await loadIndex(0);
    expect(result.data.universe).toBe(2544);
    expect(result.fromCache).toBe(false);
  });

  it('serves a fresh cached copy without asking the network', async () => {
    const fetcher = respond(INDEX);
    global.fetch = fetcher as never;
    await loadIndex(0);
    expect(fetcher).toHaveBeenCalledTimes(1);

    // Prices change once a night; a second request within the window could
    // only return what is already here.
    const again = await loadIndex(60_000);
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(again.fromCache).toBe(true);
  });

  it('falls back to the cached copy when the network fails', async () => {
    global.fetch = respond(INDEX) as never;
    await loadIndex(0);

    global.fetch = jest.fn().mockRejectedValue(new Error('offline')) as never;
    const result = await loadIndex(0);
    expect(result.data.universe).toBe(2544);
    expect(result.fromCache).toBe(true);
  });

  it('throws when there is no cache and no network', async () => {
    global.fetch = jest.fn().mockRejectedValue(new Error('offline')) as never;
    await expect(loadIndex(0)).rejects.toThrow('offline');
  });

  it('treats a non-200 as a failure rather than parsing the body', async () => {
    global.fetch = respond({ oops: true }, false, 404) as never;
    await expect(loadIndex(0)).rejects.toThrow('404');
  });
});

describe('the schema guard', () => {
  it('refuses a payload newer than this build understands', async () => {
    global.fetch = respond({ ...INDEX, schema: SUPPORTED_SCHEMA + 1 }) as never;
    await expect(loadIndex(0)).rejects.toBeInstanceOf(UnsupportedSchemaError);
  });

  it('says what to do about it, since the app cannot update itself', async () => {
    global.fetch = respond({ ...INDEX, schema: SUPPORTED_SCHEMA + 1 }) as never;
    await expect(loadIndex(0)).rejects.toThrow(/newer build/i);
  });

  it('does not hide an unreadable payload behind a stale cache', async () => {
    global.fetch = respond(INDEX) as never;
    await loadIndex(0);

    // A cache cannot rescue a format this build cannot read: showing the old
    // copy forever would hide the fact that the app needs replacing.
    global.fetch = respond({ ...INDEX, schema: SUPPORTED_SCHEMA + 1 }) as never;
    await expect(loadIndex(0)).rejects.toBeInstanceOf(UnsupportedSchemaError);
  });

  it('accepts an older payload, which this build can still read', async () => {
    global.fetch = respond({ ...INDEX, schema: SUPPORTED_SCHEMA - 1 }) as never;
    await expect(loadIndex(0)).resolves.toBeTruthy();
  });
});

describe('company files', () => {
  it('are fetched by the published id, not the ticker', async () => {
    const fetcher = respond({ schema: SUPPORTED_SCHEMA, ticker: 'M&M' });
    global.fetch = fetcher as never;
    await loadCompany('M_M', 0);
    expect(fetcher.mock.calls[0][0]).toContain('/company/M_M.json');
  });

  it('are cached separately from each other', async () => {
    global.fetch = respond({ schema: SUPPORTED_SCHEMA, ticker: 'M&M' }) as never;
    await loadCompany('M_M', 0);
    global.fetch = respond({ schema: SUPPORTED_SCHEMA, ticker: 'WELCORP' }) as never;
    await loadCompany('WELCORP', 0);

    const fetcher = jest.fn();
    global.fetch = fetcher as never;
    const first = await loadCompany('M_M', 60_000);
    expect(first.data.ticker).toBe('M&M');
    expect(fetcher).not.toHaveBeenCalled();
  });
});

describe('clearCache', () => {
  it('removes stored payloads so the next load hits the network', async () => {
    global.fetch = respond(INDEX) as never;
    await loadIndex(0);
    await clearCache();

    const fetcher = respond(INDEX);
    global.fetch = fetcher as never;
    await loadIndex(60_000);
    expect(fetcher).toHaveBeenCalled();
  });
});
