/**
 * What to be told about, and the small print.
 *
 * Each switch subscribes this device to an FCM topic. Nothing identifying is
 * sent: there is no account, no device registered anywhere, and no server that
 * knows the list -- the phone asks Google to deliver messages published under
 * a name, and the nightly job publishes to that name.
 */

import React, { useEffect, useState } from 'react';
import { Alert, ScrollView, StyleSheet, Switch, Text, View } from 'react-native';

import { clearCache, loadIndex } from '../data/remote';
import { FULL_DISCLAIMER } from '../disclaimer';
import {
  pushAvailable,
  requestPermission,
  subscribedTopics,
  toggle,
  TOPICS,
} from '../notifications';
import { radius, spacing, Theme, useTheme } from '../theme';

interface Props {
  watched: string[];
}

export function AlertsScreen({ watched }: Props) {
  const theme = useTheme();
  const styles = makeStyles(theme);
  const [active, setActive] = useState<string[]>([]);
  const [available] = useState(pushAvailable);
  const [fullDisclaimer, setFullDisclaimer] = useState<string>();

  useEffect(() => {
    subscribedTopics().then(setActive);
    // Read from the cache the Ideas screen already filled rather than taking a
    // copy through props: this screen is reachable before Ideas has loaded, and
    // the disclaimer is the one thing on it that must not be missing.
    loadIndex(Number.MAX_SAFE_INTEGER)
      .then((result) => setFullDisclaimer(result.data.disclaimer_full))
      .catch(() => setFullDisclaimer(undefined));
  }, []);

  const onToggle = async (topic: string, on: boolean) => {
    // Optimistic, then corrected from storage: the switch should move under
    // the finger rather than after a round-trip to Google.
    setActive((current) =>
      on ? [...current, topic] : current.filter((t) => t !== topic),
    );
    try {
      if (on && !(await requestPermission())) {
        Alert.alert(
          'Notifications are off',
          'Allow notifications for MCFinEx in system settings, then try again.',
        );
      } else {
        await toggle(topic, on);
      }
    } catch (error) {
      Alert.alert('Could not change that', String(error));
    } finally {
      setActive(await subscribedTopics());
    }
  };

  return (
    <ScrollView style={styles.page} contentContainerStyle={styles.content}>
      {!available ? (
        <View style={styles.banner}>
          <Text style={styles.bannerText}>
            Push is not configured in this build, so these switches will not do
            anything. The screen still works; alerts need a build with Firebase
            set up.
          </Text>
        </View>
      ) : null}

      <Text style={styles.heading}>Alerts</Text>
      {TOPICS.map((topic) => (
        <View key={topic.id} style={styles.rowCard}>
          <View style={styles.rowHead}>
            <Text style={styles.rowLabel}>{topic.label}</Text>
            <Switch
              value={active.includes(topic.id)}
              onValueChange={(on) => onToggle(topic.id, on)}
              disabled={!available}
            />
          </View>
          <Text style={styles.rowHelp}>{topic.help}</Text>
        </View>
      ))}

      <Text style={styles.heading}>Watchlist</Text>
      <View style={styles.rowCard}>
        <Text style={styles.rowHelp}>
          {watched.length
            ? `Following ${watched.length} ${watched.length === 1 ? 'company' : 'companies'}: ${watched
                .map((id) => id.replace(/_/g, '&'))
                .join(', ')}`
            : 'Nothing watched yet. Open a company and tap Watch to be told when it reaches its entry price.'}
        </Text>
      </View>

      <Text style={styles.heading}>Data</Text>
      <View style={styles.rowCard}>
        <Text
          style={styles.action}
          accessibilityRole="button"
          onPress={async () => {
            await clearCache();
            Alert.alert('Cleared', 'Pull down on Ideas to fetch a fresh copy.');
          }}
        >
          Clear the offline copy
        </Text>
        <Text style={styles.rowHelp}>
          The screen is cached on this device so it works without a connection.
          Prices refresh nightly; there is nothing newer to fetch during the day.
        </Text>
      </View>

      <Text style={styles.heading}>Disclaimer</Text>
      <Text style={styles.legal}>
        {stripMarkdown(fullDisclaimer ?? FULL_DISCLAIMER)}
      </Text>
    </ScrollView>
  );
}

/** The payload carries markdown for the web pages; this screen renders text. */
function stripMarkdown(text: string): string {
  return text.replace(/\*\*/g, '').replace(/\n{3,}/g, '\n\n').trim();
}

const makeStyles = (theme: Theme) =>
  StyleSheet.create({
    page: { backgroundColor: theme.bg },
    content: { padding: spacing.lg, paddingBottom: spacing.xl * 2 },
    banner: {
      backgroundColor: theme.warnBg,
      borderRadius: radius,
      padding: spacing.md,
      marginBottom: spacing.lg,
    },
    bannerText: { color: theme.warn, fontSize: 12, lineHeight: 17 },
    heading: {
      color: theme.faint,
      fontSize: 11,
      fontWeight: '700',
      textTransform: 'uppercase',
      marginTop: spacing.lg,
      marginBottom: spacing.sm,
    },
    rowCard: {
      backgroundColor: theme.card,
      borderColor: theme.border,
      borderWidth: StyleSheet.hairlineWidth,
      borderRadius: radius,
      padding: spacing.lg,
      marginBottom: spacing.md,
    },
    rowHead: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
    rowLabel: { color: theme.text, fontSize: 14, fontWeight: '600', flexShrink: 1 },
    rowHelp: { color: theme.muted, fontSize: 12, lineHeight: 17, marginTop: spacing.sm },
    action: { color: theme.accent, fontSize: 14, fontWeight: '600' },
    legal: { color: theme.muted, fontSize: 12, lineHeight: 18 },
  });
