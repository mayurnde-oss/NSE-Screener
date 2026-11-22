"""Point-in-time daily BB, weekly SMA-seeded EMA, momentum, and trend indicators."""
from __future__ import annotations
import numpy as np
import pandas as pd


def ema_sma_seed(close: pd.Series, period: int) -> pd.Series:
    """First EMA equals SMA of first N completed weekly closes; rest recursive."""
    v = pd.to_numeric(close, errors='coerce').to_numpy(dtype=float)
    out = np.full(len(v), np.nan)
    if len(v) < period or not np.isfinite(v).all():
        return pd.Series(out, index=close.index, dtype=float)
    out[period-1] = np.mean(v[:period])
    alpha = 2 / (period + 1)
    for i in range(period, len(v)):
        out[i] = alpha*v[i] + (1-alpha)*out[i-1]
    return pd.Series(out, index=close.index, name=f'WEMA{period}')


def add_indicators(df: pd.DataFrame, bb_period: int = 20, bb_std: float = 1.5) -> tuple[pd.DataFrame, int]:
    """W-FRI weekly closes are available only from that Friday onward.

    On other weekdays previous Friday EMA is used. On Friday after close this
    week's weekly final close becomes available. Thursday preceding Friday holiday
    conservatively uses previous week EMA.
    """
    out = df.copy().sort_index()
    c = out['Close']
    out['BB_Lower'] = c.rolling(bb_period, min_periods=bb_period).mean() - bb_std*c.rolling(bb_period, min_periods=bb_period).std(ddof=0)
    weekly = c.resample('W-FRI').last().dropna()
    for n in [150, 200]:
        em = ema_sma_seed(weekly, n)
        # Weekly timestamps always Fridays; forward-fill to completed daily dates.
        out[f'WEMA{n}'] = em.reindex(out.index, method='ffill')
    for n in (50, 150, 200):
        out[f'SMA{n}'] = c.rolling(n, min_periods=n).mean()
    out['SMA200_Rising'] = (out['SMA200'] > out['SMA200'].shift(20))
    out['High52W'] = out['High'].rolling(252, min_periods=252).max()
    out['Low52W'] = out['Low'].rolling(252, min_periods=252).min()
    for days, label in [(63, 'MOM3'), (126, 'MOM6'), (252, 'MOM12')]:
        out[label] = c / c.shift(days) - 1.0
    return out, len(weekly)


def range_to_ema_pct(low: float, high: float, ema: float) -> float:
    if not np.isfinite(ema) or ema <= 0:
        return float('nan')
    return max(float(low) - float(ema), float(ema) - float(high), 0.0) / float(ema)*100


def classify_technical(high: float, bb_lower: float, distance: float, strict_pct=2.0, near_pct=5.0) -> str:
    if not all(np.isfinite(x) for x in (high, bb_lower, distance)):
        return 'INSUFFICIENT_DATA'
    bb_strict = high < bb_lower  # equality FAILS by design
    if bb_strict and distance <= strict_pct:
        return 'STRICT_MATCH'
    if bb_strict and distance <= near_pct:
        return 'NEAR_MATCH'
    if distance <= near_pct:
        return 'APPROACHING_SUPPORT'
    return 'NO_MATCH'


def evaluate_last(df: pd.DataFrame, ticker: str, strict_pct: float, near_pct: float) -> dict:
    row = df.iloc[-1]
    close, high, low = (float(row[x]) for x in ('Close', 'High', 'Low'))
    d150 = range_to_ema_pct(low, high, float(row['WEMA150']))
    d200 = range_to_ema_pct(low, high, float(row['WEMA200']))
    distances = {'WEMA150': d150, 'WEMA200': d200}
    valid_dist = {k:v for k,v in distances.items() if np.isfinite(v)}
    nearest = min(valid_dist, key=valid_dist.get) if valid_dist else ''
    d = valid_dist.get(nearest, float('nan'))
    bb_lower = float(row['BB_Lower'])
    technical = classify_technical(high, bb_lower, d, strict_pct, near_pct)
    mom = row[['MOM3', 'MOM6', 'MOM12']]
    rs_factor = float(0.4*mom['MOM3'] + 0.3*mom['MOM6'] + 0.3*mom['MOM12']) if mom.notna().all() else float('nan')
    template_rules = {
        'close_gt_sma150_sma200': close > row['SMA150'] and close > row['SMA200'],
        'sma150_gt_sma200': row['SMA150'] > row['SMA200'],
        'sma200_rising_20d': bool(row['SMA200_Rising']),
        'sma50_gt_sma150_sma200': row['SMA50'] > row['SMA150'] and row['SMA50'] > row['SMA200'],
        'close_gt_sma50': close > row['SMA50'],
        'close_30pct_above_52wk_low': close >= 1.30*row['Low52W'],
        'close_within_25pct_52wk_high': close >= 0.75*row['High52W'],
    }
    template_rules = {key: bool(val) if pd.notna(val) else False for key, val in template_rules.items()}
    return {
        'ticker': ticker, 'signal_date': df.index[-1].date().isoformat(),
        'close': round(close, 4), 'daily_high': round(high,4), 'daily_low': round(low,4),
        'bb_lower': round(bb_lower,4) if np.isfinite(bb_lower) else None,
        'wema150': round(float(row['WEMA150']),4) if np.isfinite(row['WEMA150']) else None,
        'wema200': round(float(row['WEMA200']),4) if np.isfinite(row['WEMA200']) else None,
        'ema150_distance_pct': round(d150,3) if np.isfinite(d150) else None,
        'ema200_distance_pct': round(d200,3) if np.isfinite(d200) else None,
        'nearest_ema': nearest, 'distance_pct': round(d,3) if np.isfinite(d) else None,
        'close_to_ema150_pct': round(100*(close/row['WEMA150'] - 1),3) if np.isfinite(row['WEMA150']) else None,
        'close_to_ema200_pct': round(100*(close/row['WEMA200'] - 1),3) if np.isfinite(row['WEMA200']) else None,
        'bb_strict': bool(high < bb_lower), 'technical_bucket': technical,
        'momentum_factor': rs_factor, 'trend_template_passes_of_8': sum(template_rules.values()),
        'trend_template_rules': template_rules, 'rs_percentile': None,
        'industry_rs_percentile': None,
    }


def add_universe_ranks(records: list[dict]) -> None:
    usable = [r for r in records if np.isfinite(r['momentum_factor'])]
    if not usable:
        return
    ranked = pd.Series({r['ticker']:r['momentum_factor'] for r in usable}).rank(pct=True, method='average')*100
    for rec in records:
        rec.setdefault('rs_percentile', None)
        rec.setdefault('industry_rs_percentile', None)
        if rec['ticker'] in ranked:
            rec['rs_percentile'] = round(float(ranked.loc[rec['ticker']]), 1)
        industry = rec.get('industry')
        same = [r for r in usable if r.get('industry') == industry]
        if industry and len(same) >= 5:
            group_rank = pd.Series({r['ticker']:r['momentum_factor'] for r in same}).rank(pct=True, method='average')*100
            if rec['ticker'] in group_rank:
                rec['industry_rs_percentile'] = round(float(group_rank.loc[rec['ticker']]), 1)
        if rec.get('rs_percentile') is not None:
            rec['trend_template_passes_of_8'] += int(rec['rs_percentile'] >= 70)
