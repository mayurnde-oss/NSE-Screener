"""EOD stock scanner CLI. Fail closed on missing/stale/partial market data."""
from __future__ import annotations
import argparse
import csv
from dataclasses import replace
from datetime import date, datetime, time
import json
import logging
import math
import os
from pathlib import Path
import sys
from zoneinfo import ZoneInfo
import pandas as pd
from .config import load_settings
from .universe import load_universe
from .price_data import CSVPriceProvider, YahooResearchProvider
from .indicators import add_indicators, evaluate_last, add_universe_ranks
from .fundamentals import FundamentalCSV, score_fundamentals
from .signal_engine import make_events, history_key, base_status
from .sheets_export import GoogleSheetExporter, HEADERS, cells_for_record, HISTORY_HEADERS

LOG = logging.getLogger(__name__)
MIN_FRESH_RATIO = 0.85  # Never publish a misleading mostly empty universe.


def holiday_name(path: Path, as_of: date) -> str | None:
    if not path.exists():
        return None
    with path.open(encoding='utf-8-sig', newline='') as f:
        for row in csv.DictReader(f):
            if row.get('date','').strip() == as_of.isoformat():
                return row.get('name','Exchange holiday')
    return None


def local_history(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding='utf-8', newline='') as f:
        return [dict(r) for r in csv.DictReader(f)]


def persist_local_history(path: Path, old: list[dict], new: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    combined, seen = [], set()
    for e in old + new:
        key = history_key(e)
        if key not in seen:
            combined.append(e)
            seen.add(key)
    with path.open('w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=HISTORY_HEADERS, extrasaction='ignore')
        w.writeheader()
        w.writerows(combined)


def export_csv(path: Path, headers: list[str], rows: list[list]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8', newline='') as f:
        w = csv.writer(f)
        w.writerow(headers)
        w.writerows([[v if v is not None else '' for v in row] for row in rows])


def reject_large_recent_discontinuity(df: pd.DataFrame) -> None:
    """Splits and provider artifacts require a corporate-action check before signaling."""
    ret = df['Close'].pct_change(fill_method=None).tail(5)
    if (ret.abs() > .55).any():
        raise ValueError('Recent >55% single-session price jump; verify corporate action/adjustment before scanning')


def run(argv=None) -> int:
    parser = argparse.ArgumentParser(description='NIFTY 500 EOD BB + weekly EMA research scanner')
    parser.add_argument('--config', default='config.yaml')
    parser.add_argument('--universe', default=None, help='Manually downloaded official CSV or test universe')
    parser.add_argument('--source', choices=['csv','yahoo_research'], default=None)
    parser.add_argument('--as-of', help='Trading date YYYY-MM-DD; defaults to today Asia/Kolkata')
    parser.add_argument('--max-stocks', type=int, help='Testing only: cap universe size')
    parser.add_argument('--no-sheets', action='store_true')
    args = parser.parse_args(argv)
    settings = load_settings(Path(args.config), args.universe, args.source)
    now = datetime.now(ZoneInfo(settings.timezone))
    as_of = date.fromisoformat(args.as_of) if args.as_of else now.date()
    if as_of > now.date():
        raise ValueError('As-of date cannot be in the future')
    if as_of == now.date() and now.time() < time.fromisoformat(settings.close_time_ist):
        raise ValueError(f'Cannot scan before {settings.close_time_ist} IST market close; incomplete daily candles are prohibited')
    if as_of.weekday() >= 5:
        LOG.info('SKIPPED: %s is a weekend', as_of)
        return 0
    holiday = holiday_name(settings.path(settings.holiday_csv), as_of)
    if holiday:
        LOG.info('SKIPPED: %s: %s', as_of, holiday)
        return 0

    # Google Sheets is source of truth for cross-run event history when configured.
    exporter = GoogleSheetExporter(settings) if settings.sheets_enabled and not args.no_sheets else None
    if exporter:
        overrides = exporter.overrides()
        updates = {}
        for key, val in overrides.items():
            updates[key] = int(val) if key == 'bb_period' else val
        settings = replace(settings, **updates)
        settings.validate()
    instruments = load_universe(settings.path(settings.universe_path), args.max_stocks)
    names = {x.ticker:x for x in instruments}
    tickers = list(names)
    provider_name = settings.price_provider
    histories, source_errors = {}, {}
    if provider_name == 'csv':
        csv_provider = CSVPriceProvider(settings.path('data/prices'))
        for ticker in tickers:
            try:
                histories[ticker] = csv_provider.fetch(ticker, settings.history_start, as_of)
            except (ValueError, FileNotFoundError, pd.errors.ParserError) as exc:
                source_errors[ticker] = str(exc)
    else:
        provider = YahooResearchProvider(settings)
        histories, source_errors = provider.fetch_many(tickers, settings.history_start, as_of)

    records = []
    faults = [{'ticker': k, 'error': v} for k,v in source_errors.items()]
    for ticker in tickers:
        if ticker not in histories:
            if ticker not in source_errors:
                faults.append({'ticker':ticker, 'error':'No price history'})
            continue
        try:
            df = histories[ticker]
            if df.index[-1].date() != as_of:
                raise ValueError(f'STALE DATA: latest {df.index[-1].date()} expected {as_of}')
            reject_large_recent_discontinuity(df)
            enriched, weekly_count = add_indicators(df, settings.bb_period, settings.bb_std)
            # 150-week support is eligible independently of 200-week support.
            if weekly_count < settings.min_weekly_observations:
                enriched['WEMA200'] = float('nan')
            if weekly_count < settings.min_weekly_150_observations:
                enriched['WEMA150'] = float('nan')
            record = evaluate_last(enriched, ticker, settings.strict_proximity_pct, settings.near_proximity_pct)
            record['previous_daily_close'] = float(df['Close'].iloc[-2]) if len(df) >= 2 else None
            record['previous_daily_high'] = float(df['High'].iloc[-2]) if len(df) >= 2 else None
            if len(df) < settings.min_daily_rows or record['technical_bucket'] == 'INSUFFICIENT_DATA':
                record['technical_bucket'] = 'INSUFFICIENT_HISTORY'
                record['data_quality_note'] = (f'Only {weekly_count} weekly observations; 150W needs '
                                               f'{settings.min_weekly_150_observations}, 200W needs '
                                               f'{settings.min_weekly_observations}')
            else:
                record['data_quality_note'] = ''
            obj = names[ticker]
            record.update({'company':obj.company, 'industry':obj.industry,'exchange':obj.exchange,
                           'isin':obj.isin, 'data_provider':provider_name, 'bars':len(df),
                           'weekly_observations':weekly_count})
            records.append(record)
        except (ValueError, KeyError, TypeError, IndexError) as exc:
            faults.append({'ticker': ticker, 'error':str(exc)})
            LOG.warning('%s skipped: %s', ticker, exc)
    out = settings.path(settings.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    export_csv(out/'errors.csv', ['ticker','error'],[[x['ticker'],x['error']] for x in faults])
    required = math.ceil(len(instruments)*MIN_FRESH_RATIO)
    if len(records) < required:
        message = f'ABORTED: {len(records)}/{len(instruments)} fresh usable stocks (<85%). Results not published.'
        (out/'scan_failure.json').write_text(json.dumps({'date':as_of.isoformat(),
                                        'universe':len(instruments),'fresh':len(records),
                                        'error':message},indent=2),encoding='utf-8')
        if exporter:
            try:
                exporter.record_failure(as_of.isoformat(), message, len(instruments),
                                        len(records), now.isoformat(timespec='seconds'))
            except Exception as exc:
                LOG.error('Unable to log failure in Google Sheets: %s', exc)
        raise RuntimeError(f'{message} See {out/"errors.csv"}.')

    # Relative strength percentiles are computed on the full usable universe.
    add_universe_ranks(records)
    fundamental_csv = FundamentalCSV(settings.path(settings.fundamentals_csv))
    for r in records:
        if r['technical_bucket'] == 'STRICT_MATCH' and r['industry'] != 'Unknown':
            metrics = score_fundamentals(
                r['ticker'],r['industry'],as_of,fundamental_csv,r['rs_percentile'],
                r['industry_rs_percentile'],settings.fundamental_score_min,
                settings.fundamental_coverage_min,
            )
            r.update(metrics)
        else:
            r.update({'fundamental_score':None,'fundamental_coverage_pct':0.0,
                      'fundamental_qualified':False, 'fundamental_documented':False,
                      'fundamental_sources':'','fundamental_last_published':None})

    history_path = settings.path('data/state/signal_history.csv')
    old_history = exporter.history() if exporter else local_history(history_path)
    new_events, watchlist = make_events(records, old_history)
    # Avoid duplicate events even after partially completed remote writes.
    existing_keys = {history_key(x) for x in old_history}
    new_events = [x for x in new_events if history_key(x) not in existing_keys]
    counts = {
        'run_time_ist': now.isoformat(timespec='seconds'), 'as_of': as_of.isoformat(),
        'universe':len(instruments), 'fresh':len(records), 'failed':len(instruments)-len(records),
        'technical_strict':sum(r['technical_bucket']=='STRICT_MATCH' for r in records),
        'ineligible_history':sum(r['technical_bucket']=='INSUFFICIENT_HISTORY' for r in records),
        'below_score':sum(r['signal_status']=='TECHNICAL MATCH - FUNDAMENTALS BELOW SCORE' for r in records),
        'qualified':sum(r['signal_status']=='STRICT QUALIFIED SETUP' for r in records),
        'unverified':sum(r['signal_status']=='TECHNICAL MATCH - FUNDAMENTALS UNVERIFIED' for r in records),
        'near':sum(r['technical_bucket']=='NEAR_MATCH' for r in records),
        'approaching':sum(r['technical_bucket']=='APPROACHING_SUPPORT' for r in records),
        'new_events':len(new_events), 'data_provider':provider_name,
        'price_basis':'Yahoo OHLC auto_adjust=False' if provider_name == 'yahoo_research' else 'CSV vendor-defined',
    }
    records.sort(key=lambda x: (x['technical_bucket']!='STRICT_MATCH',
                                -(x.get('fundamental_score') or 0),
                                x.get('distance_pct') if x.get('distance_pct') is not None else 1e9))
    export_csv(out/'all_stocks.csv', HEADERS, [cells_for_record(r) for r in records])
    export_csv(out/'strict_qualified.csv', HEADERS, [cells_for_record(r) for r in records if r['signal_status']=='STRICT QUALIFIED SETUP'])
    export_csv(out/'technical_unverified.csv', HEADERS, [cells_for_record(r) for r in records if r['signal_status']=='TECHNICAL MATCH - FUNDAMENTALS UNVERIFIED'])
    export_csv(out/'technical_below_score.csv', HEADERS, [cells_for_record(r) for r in records if r['signal_status']=='TECHNICAL MATCH - FUNDAMENTALS BELOW SCORE'])
    export_csv(out/'near_matches.csv', HEADERS, [cells_for_record(r) for r in records if r['technical_bucket'] in {'NEAR_MATCH','APPROACHING_SUPPORT'}])
    (out/'run_summary.json').write_text(json.dumps(counts,indent=2),encoding='utf-8')
    if exporter:
        exporter.publish(records, new_events, counts)
        LOG.info('Published dashboard and appended %d new signal events', len(new_events))
    else:
        persist_local_history(history_path, old_history, new_events)
    print(json.dumps(counts,indent=2))
    return 0


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    try:
        sys.exit(run())
    except Exception as exc:
        LOG.exception('Scan failed closed: %s', exc)
        sys.exit(1)

if __name__ == '__main__':
    main()
