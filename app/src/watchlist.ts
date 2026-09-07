/**
 * The watchlist, held on the device.
 *
 * Nothing is sent anywhere. Per-company alerts work by subscribing to a topic
 * named after the company, so the phone tells Google "notify me about t_WELCORP"
 * without telling anyone which phone asked or what else is on the list.
 */

import AsyncStorage from '@react-native-async-storage/async-storage';

import { subscribe, tickerTopic, unsubscribe } from './notifications';

const KEY = 'mcfinex:v1:watchlist';

export async function watchlist(): Promise<string[]> {
  try {
    const raw = await AsyncStorage.getItem(KEY);
    return raw ? (JSON.parse(raw) as string[]) : [];
  } catch {
    return [];
  }
}

async function save(ids: string[]): Promise<void> {
  await AsyncStorage.setItem(KEY, JSON.stringify([...new Set(ids)].sort()));
}

/**
 * Add or remove a company, and follow its topic to match.
 *
 * The topic call is allowed to fail quietly: without Firebase configured there
 * is no subscription to make, and the watchlist is still worth keeping for
 * browsing. Silently refusing to add the company would be the worse failure.
 */
export async function setWatched(id: string, watched: boolean): Promise<string[]> {
  const current = await watchlist();
  const next = watched ? [...current, id] : current.filter((t) => t !== id);
  await save(next);
  try {
    await (watched ? subscribe : unsubscribe)(tickerTopic(id));
  } catch {
    // Push is a bonus on top of the list, not the point of it.
  }
  return next;
}
