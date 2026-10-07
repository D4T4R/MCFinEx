/**
 * One company: what the signals said, and why.
 *
 * Every signal shows its rule alongside its verdict. A BUY with no visible
 * threshold is an assertion; with the threshold it is a reading the reader can
 * disagree with, which is the only honest form for a mechanical screen.
 */

import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';

import { FlagChip, TierChip, VerdictChip } from '../components/Chips';
import { Chart } from '../components/Chart';
import { loadCompany } from '../data/remote';
import { money, percent, plain } from '../lib/format';
import { TIER_HELP, type Company, type CompanyDetail, type Trend } from '../types';

function TrendChart({ trend }: { trend: Trend }) {
  const option = {
    backgroundColor: 'transparent',
    grid: { left: 52, right: 16, top: 16, bottom: 28 },
    tooltip: { trigger: 'axis', backgroundColor: '#1e293b', borderColor: '#334155',
               textStyle: { color: '#e2e8f0', fontSize: 12 } },
    xAxis: {
      type: 'category',
      data: trend.forecast_period
        ? [...trend.periods, trend.forecast_period]
        : trend.periods,
      axisLine: { lineStyle: { color: '#334155' } },
      axisLabel: { color: '#64748b', fontSize: 10 },
    },
    yAxis: {
      type: 'value',
      splitLine: { lineStyle: { color: '#1e293b' } },
      axisLabel: { color: '#64748b', fontSize: 10 },
    },
    series: [
      {
        type: 'bar',
        data: trend.values,
        itemStyle: { color: '#3b82f6', borderRadius: [2, 2, 0, 0] },
      },
      // The forecast is drawn apart from the reported bars, in a different
      // colour: it is a projection, and a reader must not have to work out which
      // of these actually happened.
      ...(trend.forecast != null
        ? [{
            type: 'bar',
            data: [...trend.values.map(() => null), trend.forecast],
            itemStyle: {
              color: 'transparent',
              borderColor: '#fbbf24',
              borderWidth: 1.5,
              borderType: 'dashed' as const,
              borderRadius: [2, 2, 0, 0],
            },
          }]
        : []),
    ],
  };

  return (
    <div>
      <div className="mb-1 flex items-baseline justify-between">
        <h4 className="text-sm font-medium text-ink-200">{trend.label}</h4>
        <p className="text-xs text-ink-600">
          TTM {plain(trend.ttm, 0)}
          {trend.ttm_growth_pct != null && (
            <span className={trend.ttm_growth_pct >= 0 ? 'text-buy' : 'text-sell'}>
              {' '}{percent(trend.ttm_growth_pct, 1)}
            </span>
          )}
        </p>
      </div>
      <Chart option={option} style={{ height: 160 }} />
      {trend.forecast != null && (
        <p className="text-[11px] text-ink-600">
          Dashed bar is a projection for {trend.forecast_period}, confidence{' '}
          {trend.confidence.toLowerCase()}.{trend.note && ` ${trend.note}`}
        </p>
      )}
    </div>
  );
}

function Figure({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div title={hint}>
      <dt className="text-[11px] uppercase tracking-wide text-ink-600">{label}</dt>
      <dd className="tnum text-base text-ink-100">{value}</dd>
    </div>
  );
}

/**
 * Detail files are written only for companies that reached a tier -- 1,569 of
 * 2,544 -- but the screener lists and links all of them, because a distribution
 * with the untiered removed is not the market. So a company without a detail
 * file is a normal state, not an error: everything in the header comes from the
 * screen payload already in memory, and only the signal breakdown and the
 * quarterly history are missing. Saying so beats a 404.
 */
export function CompanyPage({ byId }: { byId: Map<string, Company> }) {
  const { id } = useParams<{ id: string }>();
  const summary = id ? byId.get(id) : undefined;
  const [detail, setDetail] = useState<CompanyDetail | null>(null);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    if (!id) return;
    let alive = true;
    setDetail(null);
    setError(null);
    loadCompany(id)
      .then((d) => alive && setDetail(d))
      .catch((e) => alive && setError(e instanceof Error ? e : new Error(String(e))));
    return () => { alive = false; };
  }, [id]);

  // Whichever source has it. The detail file is richer, the screen payload is
  // always there.
  const head = detail ?? summary;

  if (!head) {
    return (
      <div className="panel p-8 text-center">
        <p className="text-sm text-ink-300">
          {error ? error.message : `No company published under ${id}.`}
        </p>
        <Link to="/" className="mt-3 inline-block text-sm text-accent hover:underline">
          Back to the screener
        </Link>
      </div>
    );
  }
  if (!detail && !error) {
    return <div className="panel p-8 text-center text-sm text-ink-600">Loading…</div>;
  }

  return (
    <div className="space-y-4">
      <div className="panel p-5">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-3">
              <h1 className="text-xl font-semibold text-ink-50">{head.ticker}</h1>
              <TierChip tier={head.tier} title={TIER_HELP[head.tier]} />
            </div>
            <p className="mt-0.5 text-sm text-ink-400">
              {head.name}
              {head.sector && <span className="text-ink-600"> · {head.sector}</span>}
            </p>
            {head.flags.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-1">
                {head.flags.map((flag) => <FlagChip key={flag} flag={flag} />)}
              </div>
            )}
          </div>
          <Link to="/" className="text-sm text-ink-400 hover:text-accent">
            ← Screener
          </Link>
        </div>

        <dl className="mt-5 grid grid-cols-2 gap-5 sm:grid-cols-3 lg:grid-cols-6">
          <Figure label="Price" value={money(head.price)} />
          <Figure label="EV/EBITDA target" value={money(detail ? detail.targets.ev_ebitda : summary?.target ?? null)}
                  hint="The headline model: enterprise value from forecast EBITDA, net of cash." />
          <Figure label="Upside" value={percent(head.upside_pct, 1)} />
          <Figure label="Entry 3/4" value={money(head.entry_3by4)}
                  hint="Three quarters of the target: the looser entry." />
          <Figure label="Entry 2/3" value={money(head.entry_2by3)}
                  hint="Two thirds of the target: where the model calls it actionable." />
          <Figure label="Models agreeing" value={`${head.models_agreeing}/3`}
                  hint="EV/EBITDA, P/E on yearly earnings, P/E on quarterly." />
        </dl>
      </div>

      {!detail && (
        <div className="panel p-4">
          <p className="text-sm text-ink-300">
            The signal breakdown and quarterly history are published only for
            companies that reached a shortlist, and {head.ticker} did not on the
            last run. Everything above is from the screen itself.
          </p>
        </div>
      )}

      {detail && (
      <div className="panel overflow-hidden">
        <h2 className="border-b border-ink-800 px-4 py-2.5 text-sm font-medium text-ink-200">
          Signals · {detail.buy_signals} BUY, {detail.sell_signals} SELL of {detail.scored} scored
        </h2>
        <table className="w-full text-sm">
          <tbody>
            {detail.signals.map((signal) => (
              <tr key={signal.key} className="border-b border-ink-900 last:border-0">
                <td className="w-56 px-4 py-2 text-ink-200">{signal.label}</td>
                <td className="w-24 px-2 py-2"><VerdictChip verdict={signal.verdict} /></td>
                <td className="tnum w-24 px-2 py-2 text-right text-ink-100">
                  {signal.available ? plain(signal.value, 2) : '—'}
                </td>
                {/* The threshold, not advice. A verdict without its rule is an
                    assertion the reader cannot argue with. */}
                <td className="px-4 py-2 text-xs text-ink-600">{signal.rule}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      )}

      {detail && detail.trends.length > 0 && (
        <div className="panel p-4">
          <h2 className="mb-3 text-sm font-medium text-ink-200">Last eight quarters</h2>
          <div className="grid gap-6 lg:grid-cols-3">
            {detail.trends.map((trend) => (
              <TrendChart key={trend.label} trend={trend} />
            ))}
          </div>
        </div>
      )}

      {detail && (
        <p className="max-w-4xl text-xs leading-relaxed text-ink-600">{detail.disclaimer}</p>
      )}
    </div>
  );
}
