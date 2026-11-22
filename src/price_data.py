"""OHLCV adapters. Yahoo is opt-in personal research; CSV is provider-neutral."""
from __future__ import annotations
from datetime import date, datetime, timedelta
from pathlib import Path
import json
import logging
import re
import time
import numpy as np
import pandas as pd
from .config import Settings

LOG = logging.getLogger(__name__)
COLS = ('Open', 'High', 'Low', 'Close', 'Volume')
SAFE_TICKER = re.compile(r'^[A-Z0-9^._&-]{1,40}$')


def normalize_prices(df: pd.DataFrame, as_of: date | None = None) -> pd.DataFrame:
    if df is None or df.empty:
        raise ValueError('Empty OHLCV history')
    df = df.copy()
    if isinstance(df.columns, pd.MultiIndex):
        raise ValueError('Normalize one ticker at a time')
    df.columns = [str(c).title().strip() for c in df.columns]
    missing = set(COLS) - set(df.columns)
    if missing:
        raise ValueError(f'Missing OHLCV columns: {missing}')
    df = df.loc[:, list(COLS)]
    dates = pd.to_datetime(df.index, errors='coerce')
    if getattr(dates, 'tz', None) is not None:
        dates = dates.tz_convert('Asia/Kolkata').tz_localize(None)
    df.index = pd.DatetimeIndex(dates).normalize()
    df = df[~df.index.isna()]
    df = df[~df.index.duplicated(keep='last')].sort_index()
    if as_of is not None:
        df = df.loc[df.index.date <= as_of]
    for col in COLS:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    df = df.dropna(subset=COLS)
    valid = (df[['Open', 'High', 'Low', 'Close']].min(axis=1) > 0) & (df['Volume'] >= 0)
    valid &= df['High'] >= df[['Open', 'Close', 'Low']].max(axis=1)
    valid &= df['Low'] <= df[['Open', 'Close', 'High']].min(axis=1)
    if not valid.all():
        LOG.warning('Discarding %d invalid price candles', int((~valid).sum()))
    df = df.loc[valid]
    if df.empty:
        raise ValueError('No valid OHLCV data after validation')
    return df


class CSVPriceProvider:
    def __init__(self, folder: Path):
        self.folder = folder

    def fetch(self, ticker: str, start: str, as_of: date) -> pd.DataFrame:
        if not SAFE_TICKER.fullmatch(ticker.upper()):
            raise ValueError('Unsafe ticker filename')
        path = self.folder / f'{ticker.upper()}.csv'
        if not path.exists():
            raise FileNotFoundError(f'No CSV price file: {path}')
        raw = pd.read_csv(path)
        if 'Date' not in raw.columns:
            raise ValueError(f'{path}: missing Date column')
        raw = raw.set_index('Date')
        df = normalize_prices(raw, as_of)
        return df.loc[df.index >= pd.Timestamp(start)]


class YahooResearchProvider:
    """Opt-in unofficial research adapter; cached incremental fetches are best effort."""
    def __init__(self, settings: Settings):
        if not settings.allow_yahoo_research:
            raise ValueError('Yahoo research data adapter must be explicitly enabled')
        self.settings = settings
        self.cache_dir = settings.path(settings.price_cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _file(self, ticker: str) -> Path:
        if not SAFE_TICKER.fullmatch(ticker.upper()):
            raise ValueError(f'Invalid ticker: {ticker}')
        return self.cache_dir / (ticker.upper() + '.csv.gz')

    def _read_cache(self, ticker: str):
        p = self._file(ticker)
        meta_path = p.with_suffix('.json')
        if not (p.exists() and meta_path.exists()):
            return None, {}
        try:
            df = normalize_prices(pd.read_csv(p, index_col='Date', parse_dates=['Date']))
            meta = json.loads(meta_path.read_text(encoding='utf-8'))
            return df, meta
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            LOG.warning('Cache invalid for %s: %s', ticker, exc)
            return None, {}

    def _write_cache(self, ticker: str, df: pd.DataFrame, meta: dict):
        p = self._file(ticker)
        tmp = p.with_suffix('.tmp')
        df.rename_axis('Date').to_csv(tmp, compression='gzip')
        tmp.replace(p)
        p.with_suffix('.json').write_text(json.dumps(meta, indent=2), encoding='utf-8')

    def fetch_many(self, tickers: list[str], start: str, as_of: date) -> tuple[dict[str,pd.DataFrame], dict[str,str]]:
        import yfinance as yf
        good, errors = {}, {}
        needs = {}
        for ticker in tickers:
            cached, meta = self._read_cache(ticker)
            if cached is not None and cached.index[-1].date() >= as_of:
                good[ticker] = normalize_prices(cached, as_of)
                continue
            full_refresh = cached is None or (as_of - date.fromisoformat(meta.get('full_refreshed', '1900-01-01'))).days >= self.settings.full_refresh_days
            fetch_start = start if full_refresh else max(date.fromisoformat(start), as_of - timedelta(days=55)).isoformat()
            needs[ticker] = (cached, meta, full_refresh, fetch_start)
        by_start: dict[str, list[str]] = {}
        for ticker, (_, _, _, fetch_start) in needs.items():
            by_start.setdefault(fetch_start, []).append(ticker)
        for fetch_start, symbols in by_start.items():
            for i in range(0, len(symbols), self.settings.batch_size):
                group = symbols[i:i+self.settings.batch_size]
                fetched = None
                for attempt in range(3):
                    try:
                        fetched = yf.download(
                            tickers=group, start=fetch_start, end=(as_of+timedelta(days=1)).isoformat(),
                            interval='1d', group_by='ticker', auto_adjust=False, actions=False,
                            multi_level_index=True, threads=False, progress=False, timeout=18,
                        )
                        if fetched is not None and not fetched.empty:
                            break
                    except Exception as exc:
                        LOG.warning('Yahoo batch attempt %d failed: %s', attempt+1, exc)
                    if attempt < 2:
                        time.sleep(2**attempt)
                for ticker in group:
                    cached, meta, full_refresh, _ = needs[ticker]
                    try:
                        if fetched is None or fetched.empty:
                            raise ValueError('Batch returned no rows')
                        if isinstance(fetched.columns, pd.MultiIndex):
                            if ticker in fetched.columns.get_level_values(0):
                                stock = fetched[ticker]
                            elif ticker in fetched.columns.get_level_values(-1):
                                stock = fetched.xs(ticker, axis=1, level=-1)
                            else:
                                raise ValueError('Ticker absent from Yahoo batch response')
                        elif len(group) == 1:
                            stock = fetched
                        else:
                            raise ValueError('Unexpected batch column structure')
                        fresh = normalize_prices(stock, as_of)
                        if cached is not None and not full_refresh:
                            fresh = normalize_prices(pd.concat([cached, fresh]), as_of)
                        if len(fresh) < 1:
                            raise ValueError('No usable historical observations')
                        if full_refresh:
                            meta['full_refreshed'] = as_of.isoformat()
                        meta.update({'last_updated': as_of.isoformat(), 'provider': 'yahoo_research', 'price_basis': 'Yahoo OHLC auto_adjust=False'})
                        self._write_cache(ticker, fresh, meta)
                        good[ticker] = fresh
                    except Exception as exc:
                        # Preserve useful cached history for diagnostics but never call stale bars current.
                        if cached is not None:
                            good[ticker] = cached
                        errors[ticker] = str(exc)
        return good, errors
