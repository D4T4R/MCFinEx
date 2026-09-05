/**
 * One candidate, as a card.
 *
 * Mirrors the card in ui/ideas.py, including the part that matters most: a
 * re-rating name is described differently. Its discount is negative by
 * construction and its price-based signals are SELL, so showing the usual
 * headline numbers would present it by the very bias that hid it.
 */

import React from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import { money, percent } from '../format';
import { radius, spacing, Theme, useTheme } from '../theme';
import { Pick } from '../types';

interface Props {
  pick: Pick;
  onPress: (pick: Pick) => void;
  watched?: boolean;
}

export function PickCard({ pick, onPress, watched }: Props) {
  const theme = useTheme();
  const styles = makeStyles(theme);
  const rerating = pick.tier === 'Re-rating';

  return (
    <Pressable
      onPress={() => onPress(pick)}
      accessibilityRole="button"
      accessibilityLabel={`${pick.ticker}, ${pick.name ?? ''}`}
      style={({ pressed }) => [styles.card, pressed && styles.pressed]}
    >
      <View style={styles.header}>
        <Text style={styles.ticker} numberOfLines={1}>
          {pick.ticker}
          {watched ? ' ★' : ''}
        </Text>
        <Text style={styles.sector} numberOfLines={1}>
          {pick.sector ?? '—'}
        </Text>
      </View>
      <Text style={styles.name} numberOfLines={1}>
        {pick.name ?? ''}
      </Text>

      <View style={styles.metrics}>
        <Metric theme={theme} label="Price" value={money(pick.price)} />
        {rerating ? (
          // The discount is gone by definition, so reporting it would only ever
          // show a negative number. What is left is the distance to the target.
          <Metric
            theme={theme}
            label="Headroom"
            value={percent(pick.upside_pct)}
            tone={theme.accent}
          />
        ) : (
          <Metric
            theme={theme}
            label="vs entry"
            value={percent(pick.discount_to_entry_pct)}
            tone={
              pick.discount_to_entry_pct != null && pick.discount_to_entry_pct >= 0
                ? theme.buy
                : theme.muted
            }
          />
        )}
      </View>

      <Text style={styles.detail}>
        Target <Text style={styles.strong}>{money(pick.target)}</Text>
        {' · entry '}
        <Text style={styles.strong}>
          {money(rerating ? pick.entry_3by4 : pick.entry_2by3)}
        </Text>
        {rerating ? ' passed' : ''}
      </Text>

      <Text style={styles.detail}>
        {rerating
          ? `${pick.quality_buys}/7 quality BUY`
          : `${pick.buy_signals}/${pick.scored} BUY`}
        {` · ${pick.models_agreeing}/3 models agree`}
        {pick.sell_signals
          ? ` · ${pick.sell_signals} SELL${rerating ? ' on price' : ''}`
          : ''}
      </Text>

      {pick.flags.map((flag) => (
        <Text key={flag} style={styles.flag}>
          ⚠ {flag}
        </Text>
      ))}
    </Pressable>
  );
}

function Metric({
  theme,
  label,
  value,
  tone,
}: {
  theme: Theme;
  label: string;
  value: string;
  tone?: string;
}) {
  const styles = makeStyles(theme);
  return (
    <View style={styles.metric}>
      <Text style={styles.metricLabel}>{label}</Text>
      <Text style={[styles.metricValue, tone ? { color: tone } : null]}>{value}</Text>
    </View>
  );
}

const makeStyles = (theme: Theme) =>
  StyleSheet.create({
    card: {
      backgroundColor: theme.card,
      borderColor: theme.border,
      borderWidth: StyleSheet.hairlineWidth,
      borderRadius: radius,
      padding: spacing.lg,
      marginBottom: spacing.md,
    },
    pressed: { opacity: 0.6 },
    header: { flexDirection: 'row', justifyContent: 'space-between', gap: spacing.sm },
    ticker: { color: theme.text, fontSize: 16, fontWeight: '700', flexShrink: 1 },
    sector: { color: theme.faint, fontSize: 12, flexShrink: 1, textAlign: 'right' },
    name: { color: theme.muted, fontSize: 13, marginTop: 2 },
    metrics: { flexDirection: 'row', gap: spacing.xl, marginTop: spacing.md },
    metric: { minWidth: 96 },
    metricLabel: { color: theme.faint, fontSize: 11, textTransform: 'uppercase' },
    metricValue: {
      color: theme.text,
      fontSize: 20,
      fontWeight: '600',
      fontVariant: ['tabular-nums'],
    },
    detail: { color: theme.muted, fontSize: 13, marginTop: spacing.sm },
    strong: { color: theme.text, fontWeight: '600' },
    flag: { color: theme.warn, fontSize: 12, marginTop: spacing.xs },
  });
