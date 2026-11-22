"""Evidence-carrying fundamental CSV and conservative Minervini-inspired score.

No financial statement scraping. User-provided cells are source-documented, not
independently audited or endorsed by this software.
"""
from __future__ import annotations
from datetime import date
from pathlib import Path
import math
import pandas as pd

# Each key is a documented, point-in-time financial metric, not a live estimate.
METRIC_SCORES_NON_FINANCIAL = {
    'eps_q_yoy_pct': (15, 25),
    'eps_annual_yoy_pct': (7, 20),
    'eps_accel_pp': (3, 0),
    'revenue_q_yoy_pct': (14, 20),
    'revenue_annual_yoy_pct': (6, 15),
    'roe_pct': (10, 15),
    'roce_pct': (10, 15),
    'operating_cash_flow_positive': (8, 1),
}
METRIC_SCORES_FINANCIAL = {
    'eps_q_yoy_pct': (15, 25),
    'eps_annual_yoy_pct': (7, 20),
    'eps_accel_pp': (3, 0),
    'revenue_q_yoy_pct': (14, 20),
    'revenue_annual_yoy_pct': (6, 15),
    'roe_pct': (12, 15),
    'roa_pct': (8, 1),
    'capital_adequacy_pct': (8, 12),
}
QUARTERLY_METRICS = {'eps_q_yoy_pct', 'eps_accel_pp', 'revenue_q_yoy_pct'}
ANNUAL_METRICS = {'eps_annual_yoy_pct', 'revenue_annual_yoy_pct',
                  'roe_pct', 'roce_pct', 'roa_pct', 'operating_cash_flow_positive',
                  'capital_adequacy_pct'}


def is_financial(industry: str) -> bool:
    s = industry.lower()
    return any(x in s for x in ('financial', 'bank', 'nbfc', 'insurance', 'finserv', 'finance'))


def is_insurance(industry: str) -> bool:
    return 'insurance' in industry.lower()


class FundamentalCSV:
    def __init__(self, path: Path):
        self.path = path
        if not path.exists():
            self.frame = pd.DataFrame(columns=['ticker', 'metric', 'value', 'period_end', 'published_at', 'source_url'])
            return
        df = pd.read_csv(path, dtype=str, keep_default_na=False)
        needed = {'ticker','metric','value','period_end','published_at','source_url'}
        if not needed.issubset(df.columns):
            raise ValueError(f'Fundamentals CSV missing columns {sorted(needed - set(df.columns))}')
        df['ticker'] = df['ticker'].str.upper().str.strip()
        df['metric'] = df['metric'].str.strip()
        self.frame = df

    def documented_as_of(self, ticker: str, on_date: date) -> dict[str, dict]:
        out = {}
        rows = self.frame[self.frame['ticker'] == ticker.upper()]
        for _, row in rows.iterrows():
            metric = str(row['metric'])
            if metric not in METRIC_SCORES_NON_FINANCIAL and metric not in METRIC_SCORES_FINANCIAL:
                continue
            try:
                val = float(row['value'])
                pub = date.fromisoformat(str(row['published_at']))
                end = date.fromisoformat(str(row['period_end']))
                source = str(row['source_url']).strip()
                if not math.isfinite(val) or not source.startswith('https://'):
                    continue
                if pub > on_date or end > on_date or pub < end:
                    continue
                max_age = 210 if metric in QUARTERLY_METRICS else 570
                if (on_date-pub).days > max_age:
                    continue
                # Most recent genuinely published observation wins.
                if metric not in out or pub > out[metric]['published_at']:
                    out[metric] = {'value': val, 'published_at':pub, 'period_end':end,
                                   'source_url':source}
            except (ValueError, TypeError):
                continue
        return out


def score_fundamentals(ticker: str, industry: str, as_of: date, csv: FundamentalCSV,
                       rs_percentile: float | None, industry_rs_percentile: float | None,
                       min_score=70., min_coverage=70.) -> dict:
    financial = is_financial(industry)
    thresholds = METRIC_SCORES_FINANCIAL if financial else METRIC_SCORES_NON_FINANCIAL
    evidence = csv.documented_as_of(ticker, as_of)
    covered, score = 0., 0.
    earned = {}
    for metric, (points, threshold) in thresholds.items():
        info = evidence.get(metric)
        if info is None:
            earned[metric] = None
            continue
        covered += points
        # accel 0: non-negative acceleration; cash-flow-positive 1 means True.
        value = info['value']
        points_earned = points if value >= threshold else 0.0
        score += points_earned
        earned[metric] = points_earned
    if rs_percentile is not None and math.isfinite(float(rs_percentile)):
        covered += 20
        rs_points = min(20, max(0, 0.2*float(rs_percentile)))
        score += rs_points
        earned['custom_relative_strength'] = round(rs_points, 2)
    else:
        earned['custom_relative_strength'] = None
    if industry_rs_percentile is not None and math.isfinite(float(industry_rs_percentile)):
        covered += 7
        leader_points = min(7, max(0, 0.07*float(industry_rs_percentile)))
        score += leader_points
        earned['industry_leadership'] = round(leader_points, 2)
    else:
        earned['industry_leadership'] = None
    essentials = ['eps_q_yoy_pct', 'revenue_q_yoy_pct', 'roe_pct',
                  'roa_pct' if financial else 'roce_pct']
    essential_ok = all(metric in evidence for metric in essentials)
    # Insurance accounting differs fundamentally; do not silently apply banking rules.
    if is_insurance(industry):
        essential_ok = False
    coverage_ok = covered >= min_coverage
    qualified = bool(essential_ok and coverage_ok and score >= min_score)
    return {
        'fundamental_score': round(score, 2), 'fundamental_coverage_pct': round(covered, 1),
        'fundamental_documented': bool(essential_ok and coverage_ok),
        'fundamental_qualified': qualified, 'fundamental_missing_essentials': ','.join(x for x in essentials if x not in evidence),
        'fundamental_notes': 'Insurance needs a specialized verified rubric' if is_insurance(industry) else '',
        'eps_q_yoy_pct': evidence.get('eps_q_yoy_pct', {}).get('value'),
        'revenue_q_yoy_pct': evidence.get('revenue_q_yoy_pct', {}).get('value'),
        'roe_pct': evidence.get('roe_pct', {}).get('value'),
        'roce_pct': evidence.get('roce_pct', {}).get('value'),
        'roa_pct': evidence.get('roa_pct', {}).get('value'),
        'fundamental_sources': '; '.join(sorted({v['source_url'] for v in evidence.values()})),
        'fundamental_last_published': max((v['published_at'] for v in evidence.values()), default=None).isoformat()
                                      if evidence else None,
        'fundamental_metric_detail': '; '.join(f'{name}={evidence[name]["value"]} [pub {evidence[name]["published_at"]}]'
                                              for name in sorted(evidence)),
        'fundamental_points_breakdown': earned,
    }
