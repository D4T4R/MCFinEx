/**
 * Push, by topic rather than by device token.
 *
 * A token-based design needs a server: somewhere to register the token, a
 * database to keep it in, and a write endpoint exposed to the internet. Topics
 * invert that -- the phone subscribes itself, and the sender publishes to a
 * name. There is nothing to register with, so there is no device table, no
 * write endpoint, and no record anywhere of who is running this app.
 *
 * Everything here degrades to a no-op when Firebase is not configured, so the
 * rest of the app runs in Expo Go, on web, and in a build with no
 * google-services.json.
 */

import { PermissionsAndroid, Platform } from 'react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';

export interface TopicOption {
  id: string;
  label: string;
  help: string;
}

/** Must match the topics `mcfinex notify` publishes to. */
export const TOPICS: TopicOption[] = [
  {
    id: 'entry-reached',
    label: 'Entry price reached',
    help: 'A tracked company fell to its 2/3 entry price. The model calls this the point it becomes actionable.',
  },
  {
    id: 'high-conviction',
    label: 'New high-conviction name',
    help: 'A company entered the top tier, having not been there on the previous run.',
  },
  {
    id: 'daily-pick',
    label: 'Daily pick',
    help: 'The best-corroborated name each day, sent whether or not anything changed.',
  },
];

/** Per-company topic for a watchlist entry, keyed by the published file id. */
export const tickerTopic = (id: string) => `t_${id}`;

const SUBSCRIPTIONS_KEY = 'mcfinex:v1:topics';

/**
 * Firebase, or null when it is not installed or not configured.
 *
 * Required lazily and cached: a static import would crash on web and in any
 * build without native Firebase, taking the whole app with it rather than just
 * the notification settings.
 */
let cached: unknown | null | undefined;

function messaging(): any | null {
  if (cached === undefined) {
    try {
      // eslint-disable-next-line @typescript-eslint/no-var-requires
      const module = require('@react-native-firebase/messaging');
      const factory = module.default ?? module;
      // Touching the instance is what actually fails when there is no
      // google-services.json, so it has to happen inside the try.
      factory().app;
      cached = factory;
    } catch {
      cached = null;
    }
  }
  return (cached as any) ?? null;
}

export function pushAvailable(): boolean {
  return messaging() != null;
}

/**
 * Ask for permission to show notifications.
 *
 * Android 13 made this a runtime permission; before that, subscribing was
 * enough. iOS has always asked. Returns whether notifications may be shown.
 */
export async function requestPermission(): Promise<boolean> {
  const fcm = messaging();
  if (!fcm) return false;

  if (Platform.OS === 'android' && Number(Platform.Version) >= 33) {
    const granted = await PermissionsAndroid.request(
      PermissionsAndroid.PERMISSIONS.POST_NOTIFICATIONS,
    );
    if (granted !== PermissionsAndroid.RESULTS.GRANTED) return false;
  }

  const status = await fcm().requestPermission();
  // 1 = AUTHORIZED, 2 = PROVISIONAL in the Firebase enum.
  return status === 1 || status === 2;
}

/**
 * Which topics this device is subscribed to.
 *
 * Kept on the device because FCM offers no way to ask. The subscription lives
 * on Google's side; this is only our record of what we asked for, which is why
 * it is written after the call succeeds rather than before.
 */
export async function subscribedTopics(): Promise<string[]> {
  try {
    const raw = await AsyncStorage.getItem(SUBSCRIPTIONS_KEY);
    return raw ? (JSON.parse(raw) as string[]) : [];
  } catch {
    return [];
  }
}

async function remember(topics: string[]): Promise<void> {
  await AsyncStorage.setItem(SUBSCRIPTIONS_KEY, JSON.stringify([...new Set(topics)]));
}

export async function subscribe(topic: string): Promise<boolean> {
  const fcm = messaging();
  if (!fcm) return false;
  await fcm().subscribeToTopic(topic);
  await remember([...(await subscribedTopics()), topic]);
  return true;
}

export async function unsubscribe(topic: string): Promise<boolean> {
  const fcm = messaging();
  if (!fcm) return false;
  await fcm().unsubscribeFromTopic(topic);
  await remember((await subscribedTopics()).filter((t) => t !== topic));
  return true;
}

export async function toggle(topic: string, on: boolean): Promise<boolean> {
  return on ? subscribe(topic) : unsubscribe(topic);
}
