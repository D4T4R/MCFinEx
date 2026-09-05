/**
 * The shortlist.
 *
 * Deliberately not the whole universe: 1,569 of 2,544 names reach some tier, so
 * the job is to narrow rather than to display. Ranked by corroboration before
 * size of upside, which is the ordering picks.rank() already applies -- the app
 * does not re-sort, because a 400% upside on one model with two BUY signals is
 * noise and sorting on upside would put it top.
 */

import React, { useCallback, useMemo, useState } from 'react';
import {
  ActivityIndicator,
  FlatList,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';

import { PickCard } from '../components/PickCard';
import { loadIndex } from '../data/remote';
import { useLoader } from '../data/useLoader';
import { freshness } from '../format';
import { radius, spacing, Theme, useTheme } from '../theme';
import { IndexPayload, Pick, Tier, TIER_HELP, TIERS } from '../types';

interface Props {
  onOpen: (pick: Pick) => void;
  watched: string[];
}

export function IdeasScreen({ onOpen, watched }: Props) {
  const theme = useTheme();
  const styles = makeStyles(theme);
  const [tier, setTier] = useState<Tier>('High conviction');
  const [query, setQuery] = useState('');

  const state = useLoader<IndexPayload>(useCallback((age) => loadIndex(age), []), []);
  const { data } = state;

  const shortlist = useMemo(() => {
    if (!data) return [];
    const needle = query.trim().toUpperCase();
    return data.picks.filter(
      (p) =>
        p.tier === tier &&
        (!needle ||
          p.ticker.includes(needle) ||
          (p.name ?? '').toUpperCase().includes(needle)),
    );
  }, [data, tier, query]);

  if (state.loading && !data) {
    return (
      <View style={styles.centre}>
        <ActivityIndicator color={theme.accent} />
      </View>
    );
  }

  if (!data) {
    return (
      <View style={styles.centre}>
        <Text style={styles.errorTitle}>Nothing to show</Text>
        <Text style={styles.errorBody}>{state.error?.message ?? 'No data available.'}</Text>
      </View>
    );
  }

  return (
    <FlatList
      data={shortlist}
      keyExtractor={(p) => p.id}
      style={styles.list}
      contentContainerStyle={styles.listContent}
      refreshControl={
        <RefreshControl
          refreshing={state.refreshing}
          onRefresh={state.refresh}
          tintColor={theme.accent}
        />
      }
      ListHeaderComponent={
        <View>
          <Text style={styles.caption}>
            Ranked by corroboration, not by size of upside.{' '}
            {freshness(data.price_date, data.last_scraped)}
          </Text>

          {/* Above the cards, not below them: a reader who scrolls straight to
              the list should still have met the caveat. */}
          <View style={styles.disclaimer}>
            <Text style={styles.disclaimerText}>{data.disclaimer}</Text>
          </View>

          {state.error ? (
            <Text style={styles.offline}>
              Showing the last copy on this device — {state.error.message}
            </Text>
          ) : null}

          <Counts data={data} theme={theme} />

          <ScrollView
            horizontal
            showsHorizontalScrollIndicator={false}
            contentContainerStyle={styles.tabs}
          >
            {TIERS.map((name) => (
              <Text
                key={name}
                onPress={() => setTier(name)}
                accessibilityRole="button"
                style={[styles.tab, tier === name && styles.tabActive]}
              >
                {name} {data.tiers[name] ?? 0}
              </Text>
            ))}
          </ScrollView>

          <Text style={styles.help}>{TIER_HELP[tier]}</Text>

          <TextInput
            value={query}
            onChangeText={setQuery}
            placeholder="Filter by ticker or name"
            placeholderTextColor={theme.faint}
            autoCapitalize="characters"
            autoCorrect={false}
            style={styles.search}
          />
        </View>
      }
      ListEmptyComponent={
        <Text style={styles.empty}>
          {query
            ? `Nothing in ${tier} matches “${query}”.`
            : 'Nothing qualifies at this tier right now.'}
        </Text>
      }
      renderItem={({ item }) => (
        <PickCard pick={item} onPress={onOpen} watched={watched.includes(item.id)} />
      )}
    />
  );
}

function Counts({ data, theme }: { data: IndexPayload; theme: Theme }) {
  const styles = makeStyles(theme);
  const cells: [string, string | number][] = [
    ['Universe', data.universe.toLocaleString('en-IN')],
    ['High conviction', data.tiers['High conviction'] ?? 0],
    ['Re-rating', data.tiers['Re-rating'] ?? 0],
  ];
  return (
    <View style={styles.counts}>
      {cells.map(([label, value]) => (
        <View key={label} style={styles.count}>
          <Text style={styles.countValue}>{value}</Text>
          <Text style={styles.countLabel}>{label}</Text>
        </View>
      ))}
    </View>
  );
}

const makeStyles = (theme: Theme) =>
  StyleSheet.create({
    list: { backgroundColor: theme.bg },
    listContent: { padding: spacing.lg, paddingBottom: spacing.xl * 2 },
    centre: {
      flex: 1,
      alignItems: 'center',
      justifyContent: 'center',
      backgroundColor: theme.bg,
      padding: spacing.xl,
    },
    errorTitle: { color: theme.text, fontSize: 18, fontWeight: '700', marginBottom: spacing.sm },
    errorBody: { color: theme.muted, fontSize: 14, textAlign: 'center' },
    caption: { color: theme.faint, fontSize: 12, marginBottom: spacing.md },
    disclaimer: {
      backgroundColor: theme.warnBg,
      borderRadius: radius,
      padding: spacing.md,
      marginBottom: spacing.md,
    },
    disclaimerText: { color: theme.warn, fontSize: 12, lineHeight: 17 },
    offline: { color: theme.warn, fontSize: 12, marginBottom: spacing.md },
    counts: { flexDirection: 'row', gap: spacing.xl, marginBottom: spacing.lg },
    count: {},
    countValue: {
      color: theme.text,
      fontSize: 22,
      fontWeight: '700',
      fontVariant: ['tabular-nums'],
    },
    countLabel: { color: theme.faint, fontSize: 11 },
    tabs: { gap: spacing.sm, paddingRight: spacing.lg },
    tab: {
      color: theme.muted,
      backgroundColor: theme.card,
      borderColor: theme.border,
      borderWidth: StyleSheet.hairlineWidth,
      borderRadius: radius,
      paddingHorizontal: spacing.md,
      paddingVertical: spacing.sm,
      fontSize: 13,
      overflow: 'hidden',
    },
    tabActive: { color: theme.card, backgroundColor: theme.accent, borderColor: theme.accent },
    help: { color: theme.faint, fontSize: 12, marginTop: spacing.md, lineHeight: 17 },
    search: {
      color: theme.text,
      backgroundColor: theme.card,
      borderColor: theme.border,
      borderWidth: StyleSheet.hairlineWidth,
      borderRadius: radius,
      paddingHorizontal: spacing.md,
      paddingVertical: spacing.sm,
      marginVertical: spacing.md,
    },
    empty: { color: theme.muted, fontSize: 14, textAlign: 'center', marginTop: spacing.xl },
  });
