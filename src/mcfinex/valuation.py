"""EV/EBITDA and EPS valuation models.

Every series here is **chronological: oldest first, newest last**. Growth is a
fraction (0.12 means +12%), never a percentage, right up until a field is
explicitly named ``_pct``. The original Java mixed both conventions, which is
where several of its unit bugs came from -- see README "Corrections".
"""

from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from typing import Iterable, Sequence


def _finite(x: float | None) -> bool:
    return x is not None and math.isfinite(x)


def growth_series(values: Sequence[float | None]) -> list[float | None]:
    """Period-over-period growth as fractions, oldest first.

    ``None`` where the prior period is missing, zero, or non-finite -- those
    are gaps, not zeros, and averaging must skip them rather than drag the mean
    toward zero.
    """
    out: list[float | None] = []
    for prev, curr in zip(values, values[1:]):
        if not _finite(prev) or not _finite(curr) or prev == 0:
            out.append(None)
            continue
        # A negative base inverts the sign of the ratio; multiplying by sign(prev)
        # keeps "got better" positive even when earnings were negative.
        out.append((curr - prev) / prev * (1.0 if prev > 0 else -1.0))
    return out


def mean(values: Iterable[float | None]) -> float | None:
    """Arithmetic mean over the finite entries, or ``None`` if there are none."""
    vals = [v for v in values if _finite(v)]
    return sum(vals) / len(vals) if vals else None


@dataclass
class EvEbitdaValuation:
    ebitda: list[float | None]
    ebitda_growth: list[float | None]
    average_ebitda_growth: float | None
    expected_ebitda: float | None
    forecast_ev: float | None
    target_price: float | None
    target_price_with_borrowing: float | None
    entry_price_1by4: float | None
    entry_price_with_borrowing_1by4: float | None
    entry_price_1by3: float | None
    entry_price_with_borrowing_1by3: float | None
    difference_pct: float | None
    difference_with_borrowing_pct: float | None

    as_dict = asdict


def value_by_ev_ebitda(
    ebitda: Sequence[float | None],
    *,
    ev_ebitda_multiple: float | None,
    outstanding_shares: float | None,
    long_term_borrowings: float | None,
    current_price: float | None,
) -> EvEbitdaValuation:
    """Forecast an EV/EBITDA target price.

    Grows the newest EBITDA by its historical average growth, re-applies the
    current EV/EBITDA multiple to get a forecast enterprise value, then divides
    into per-share terms. ``ebitda`` is oldest-first.
    """
    growth = growth_series(ebitda)
    avg_growth = mean(growth)

    latest = ebitda[-1] if ebitda and _finite(ebitda[-1]) else None
    expected = latest * (1 + avg_growth) if _finite(latest) and _finite(avg_growth) else None
    forecast_ev = expected * ev_ebitda_multiple if _finite(expected) and _finite(ev_ebitda_multiple) else None

    shares = outstanding_shares if _finite(outstanding_shares) and outstanding_shares else None
    target = forecast_ev / shares if _finite(forecast_ev) and shares else None

    # Equity value is enterprise value less net debt, so borrowings subtract
    # from the forecast EV. The Java computed (borrowings - EV), which produced
    # negative target prices for every leveraged company.
    debt = long_term_borrowings if _finite(long_term_borrowings) else 0.0
    target_debt = (forecast_ev - debt) / shares if _finite(forecast_ev) and shares else None

    def pct_gap(t: float | None) -> float | None:
        if not _finite(t) or not _finite(current_price) or not current_price:
            return None
        return (t - current_price) / current_price * 100

    def scale(t: float | None, f: float) -> float | None:
        return t * f if _finite(t) else None

    return EvEbitdaValuation(
        ebitda=list(ebitda),
        ebitda_growth=growth,
        average_ebitda_growth=avg_growth,
        expected_ebitda=expected,
        forecast_ev=forecast_ev,
        target_price=target,
        target_price_with_borrowing=target_debt,
        entry_price_1by4=scale(target, 0.75),
        entry_price_with_borrowing_1by4=scale(target_debt, 0.75),
        entry_price_1by3=scale(target, 0.66),
        entry_price_with_borrowing_1by3=scale(target_debt, 0.66),
        difference_pct=pct_gap(target),
        difference_with_borrowing_pct=pct_gap(target_debt),
    )


@dataclass
class EpsValuation:
    eps: list[float | None]
    growth: list[float | None]
    eps_growth: float | None
    forward_eps: float | None
    current_pe: float | None
    forward_pe: float | None
    difference_in_pe_pct: float | None
    target_price: float | None

    as_dict = asdict


def value_by_eps(eps: Sequence[float | None], *, current_price: float | None) -> EpsValuation:
    """Forecast a PE-rerating target price from an EPS series (oldest first)."""
    growth = growth_series(eps)
    avg_growth = mean(growth)

    latest = eps[-1] if eps and _finite(eps[-1]) else None
    forward_eps = latest * (1 + avg_growth) if _finite(latest) and _finite(avg_growth) else None

    price = current_price if _finite(current_price) else None
    current_pe = price / latest if price and _finite(latest) and latest else None
    forward_pe = price / forward_eps if price and _finite(forward_eps) and forward_eps else None

    diff_pct = (
        (current_pe - forward_pe) / current_pe * 100
        if _finite(current_pe) and _finite(forward_pe) and current_pe
        else None
    )
    target = price * (1 + diff_pct / 100) if price and _finite(diff_pct) else None

    return EpsValuation(
        eps=list(eps),
        growth=growth,
        eps_growth=avg_growth,
        forward_eps=forward_eps,
        current_pe=current_pe,
        forward_pe=forward_pe,
        difference_in_pe_pct=diff_pct,
        target_price=target,
    )
