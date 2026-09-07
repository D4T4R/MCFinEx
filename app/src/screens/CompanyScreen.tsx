/**
 * One company: what the model says, and why.
 *
 * The signals list is the point of the screen. A tier is a summary, and a
 * reader deciding whether to trust it needs to see which of the ten measures
 * produced it -- including the ones that returned UNKNOWN, which are withheld
 * rather than estimated and are not neutral readings.
 */

import React, { useCallback } from 'react';
import {
  ActivityIndicator,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';

import { loadCompany } from '../data/remote';
import { useLoader } from '../data/useLoader';
import { compact, money, percent } from '../format';
import { radius, spacing, Theme, useTheme, verdictColour } from '../theme';
import { Company, Pick, Signal, Trend } from '../types';

interface Props {
  pick: Pick;
  watched: boolean;
  onToggleWatch: (id: string) => void;
}

export function CompanyScreen({ pick, watched, onToggleWatch }: Props) {
  const theme = useTheme();
  const styles = makeStyles(theme);
  const state = useLoader<Company>(
    useCallback((age) => loadCompany(pick.id, age), [pick.id]),
    [pick.id],
  );

  if (state.loading && !state.data) {
    return (
      <View style={styles.centre}>
        <ActivityIndicator color={theme.accent} />
      </View>
    );
  }

  const company = state.data;
  if (!company) {
    return (
      <View style={styles.centre}>
        <Text style={styles.errorTitle}>Could not load {pick.ticker}</Text>
        <Text style={styles.errorBody}>{state.error?.message}</Text>
      </View>
    );
  }

  return (
    <ScrollView
      style={styles.page}
      contentContainerStyle={styles.content}
      refreshControl={
        <RefreshControl
          refreshing={state.refreshing}
          onRefresh={state.refresh}
          tintColor={theme.accent}
        />
      }
    >
      <Text style={styles.name}>{company.name ?? pick.ticker}</Text>
      <Text style={styles.sub}>
        {company.sector ?? '—'} · {company.tier}
      </Text>

      <Pressable
        onPress={() => onToggleWatch(pick.id)}
        accessibilityRole="button"
        style={({ pressed }) => [styles.watch, pressed && { opacity: 0.6 }]}
      >
        <Text style={styles.watchText}>
          {watched ? '★  Watching — alerts on' : '☆  Watch for alerts'}
        </Text>
      </Pressable>

      <View style={styles.row}>
        <Figure theme={theme} label="Price" value={money(company.price)} />
        <Figure theme={theme} label="Target" value={money(company.target)} />
        <Figure
          theme={theme}
          label="Upside"
          value={percent(company.upside_pct)}
          tone={theme.accent}
        />
      </View>
      <View style={styles.row}>
        <Figure theme={theme} label="Entry 2/3" value={money(company.entry_2by3)} />
        <Figure theme={theme} label="Entry 3/4" value={money(company.entry_3by4)} />
        <Figure
          theme={theme}
          label="vs entry"
          value={percent(company.discount_to_entry_pct)}
        />
      </View>

      {company.flags.length ? (
        <View style={styles.flags}>
          {company.flags.map((flag) => (
            <Text key={flag} style={styles.flag}>
              ⚠ {flag}
            </Text>
          ))}
        </View>
      ) : null}

      <Section title="Targets by model" theme={theme}>
        <Row theme={theme} label="EV/EBITDA" value={money(company.targets.ev_ebitda)} />
        <Row theme={theme} label="EPS yearly" value={money(company.targets.pe_yearly)} />
        <Row theme={theme} label="EPS quarterly" value={money(company.targets.pe_quarterly)} />
        <Text style={styles.note}>
          {company.models_agreeing}/3 models put fair value above the current price.
        </Text>
      </Section>

      <Section
        title={`Signals — ${company.buy_signals}/${company.scored} BUY`}
        theme={theme}
      >
        {company.signals.map((signal) => (
          <SignalRow key={signal.key} signal={signal} theme={theme} />
        ))}
      </Section>

      {company.trends.map((trend) => (
        <Section key={trend.label} title={trend.label} theme={theme}>
          <TrendBody trend={trend} theme={theme} />
        </Section>
      ))}

      <Text style={styles.disclaimer}>{company.disclaimer}</Text>
    </ScrollView>
  );
}

function SignalRow({ signal, theme }: { signal: Signal; theme: Theme }) {
  const styles = makeStyles(theme);
  return (
    <View style={styles.signal}>
      <View style={styles.signalHead}>
        <Text style={styles.signalLabel}>{signal.label}</Text>
        <Text style={[styles.verdict, { color: verdictColour(theme, signal.verdict) }]}>
          {signal.verdict}
        </Text>
      </View>
      <Text style={styles.signalRule}>
        {signal.available ? `${compact(signal.value)} · ${signal.rule}` : signal.rule}
      </Text>
    </View>
  );
}

function TrendBody({ trend, theme }: { trend: Trend; theme: Theme }) {
  const styles = makeStyles(theme);
  const last = trend.periods.length - 1;
  return (
    <View>
      <Row theme={theme} label="Latest" value={
        `${trend.periods[last] ?? '—'} · ${compact(trend.values[last])}`
      } />
      <Row theme={theme} label="TTM" value={compact(trend.ttm)} />
      <Row theme={theme} label="TTM growth" value={percent(trend.ttm_growth_pct, 1)} />
      {trend.forecast != null ? (
        <Row
          theme={theme}
          label={`Forecast ${trend.forecast_period ?? ''}`.trim()}
          value={`${compact(trend.forecast)} · ${trend.confidence.toLowerCase()} confidence`}
        />
      ) : null}
      {trend.note ? <Text style={styles.note}>{trend.note}</Text> : null}
    </View>
  );
}

function Section({
  title,
  theme,
  children,
}: {
  title: string;
  theme: Theme;
  children: React.ReactNode;
}) {
  const styles = makeStyles(theme);
  return (
    <View style={styles.section}>
      <Text style={styles.sectionTitle}>{title}</Text>
      {children}
    </View>
  );
}

function Row({ theme, label, value }: { theme: Theme; label: string; value: string }) {
  const styles = makeStyles(theme);
  return (
    <View style={styles.kv}>
      <Text style={styles.kvLabel}>{label}</Text>
      <Text style={styles.kvValue}>{value}</Text>
    </View>
  );
}

function Figure({
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
    <View style={styles.figure}>
      <Text style={styles.figureLabel}>{label}</Text>
      <Text style={[styles.figureValue, tone ? { color: tone } : null]}>{value}</Text>
    </View>
  );
}

const makeStyles = (theme: Theme) =>
  StyleSheet.create({
    page: { backgroundColor: theme.bg },
    content: { padding: spacing.lg, paddingBottom: spacing.xl * 2 },
    centre: {
      flex: 1,
      alignItems: 'center',
      justifyContent: 'center',
      backgroundColor: theme.bg,
      padding: spacing.xl,
    },
    errorTitle: { color: theme.text, fontSize: 17, fontWeight: '700' },
    errorBody: { color: theme.muted, fontSize: 13, marginTop: spacing.sm, textAlign: 'center' },
    name: { color: theme.text, fontSize: 20, fontWeight: '700' },
    sub: { color: theme.faint, fontSize: 13, marginTop: 2 },
    watch: {
      borderColor: theme.border,
      borderWidth: StyleSheet.hairlineWidth,
      borderRadius: radius,
      paddingVertical: spacing.md,
      alignItems: 'center',
      marginTop: spacing.md,
      backgroundColor: theme.card,
    },
    watchText: { color: theme.accent, fontSize: 14, fontWeight: '600' },
    row: { flexDirection: 'row', gap: spacing.lg, marginTop: spacing.lg },
    figure: { flex: 1 },
    figureLabel: { color: theme.faint, fontSize: 11, textTransform: 'uppercase' },
    figureValue: {
      color: theme.text,
      fontSize: 17,
      fontWeight: '600',
      fontVariant: ['tabular-nums'],
    },
    flags: { marginTop: spacing.lg },
    flag: { color: theme.warn, fontSize: 12, marginTop: spacing.xs },
    section: {
      backgroundColor: theme.card,
      borderColor: theme.border,
      borderWidth: StyleSheet.hairlineWidth,
      borderRadius: radius,
      padding: spacing.lg,
      marginTop: spacing.lg,
    },
    sectionTitle: {
      color: theme.text,
      fontSize: 14,
      fontWeight: '700',
      marginBottom: spacing.md,
    },
    kv: {
      flexDirection: 'row',
      justifyContent: 'space-between',
      paddingVertical: spacing.xs,
      gap: spacing.md,
    },
    kvLabel: { color: theme.muted, fontSize: 13, flexShrink: 1 },
    kvValue: { color: theme.text, fontSize: 13, fontVariant: ['tabular-nums'] },
    signal: { paddingVertical: spacing.sm },
    signalHead: { flexDirection: 'row', justifyContent: 'space-between', gap: spacing.md },
    signalLabel: { color: theme.text, fontSize: 13, flexShrink: 1 },
    verdict: { fontSize: 12, fontWeight: '700' },
    signalRule: { color: theme.faint, fontSize: 11, marginTop: 2 },
    note: { color: theme.faint, fontSize: 11, marginTop: spacing.sm, lineHeight: 16 },
    disclaimer: {
      color: theme.faint,
      fontSize: 11,
      lineHeight: 16,
      marginTop: spacing.xl,
    },
  });
