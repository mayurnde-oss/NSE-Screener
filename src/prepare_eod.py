"""Prepare research CSV inputs from the published Nifty list and TejHQ EOD files."""
import argparse
from datetime import date, datetime
from io import BytesIO
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from urllib.request import urlopen
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from .price_data import SAFE_TICKER
from .constituent_sources import resolve_universe

CONSTITUENTS = 'https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv'
DATASET = 'tejhq/indian-markets'
COLUMNS = ['symbol', 'isin', 'series', 'date', 'open', 'high', 'low', 'close',
           'volume', 'adj_factor_cumulative']


def download(url):
    for attempt in range(3):
        try:
            with urlopen(url, timeout=90) as response:
                return response.read()
        except OSError:
            if attempt == 2:
                raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--as-of', type=date.fromisoformat, default=datetime.now(ZoneInfo('Asia/Kolkata')).date())
    args = parser.parse_args()
    root = Path('data')
    universe_path = root / 'nifty500.csv'
    content, source, source_meta = resolve_universe(root)
    universe = (pd.read_csv(BytesIO(content), dtype=str, keep_default_na=False)
                if content is not None else pd.DataFrame({'Symbol':source_meta['symbols'], 'ISIN Code':''}))
    metadata = json.loads(download('https://huggingface.co/api/datasets/' + DATASET))
    revision = metadata['sha']
    if not re.fullmatch('[0-9a-f]{40}', revision):
        raise ValueError('Invalid EOD dataset revision')
    frames = []
    for year in range(2015, args.as_of.year + 1):
        url = f'https://huggingface.co/datasets/{DATASET}/resolve/{revision}/prices_adjusted/nse_{year}.parquet'
        frame = pq.read_table(BytesIO(download(url)), columns=COLUMNS).to_pandas()
        relevant = frame['isin'].isin(universe['ISIN Code']) | frame.symbol.isin(universe.Symbol)
        frames.append(frame[relevant].copy())
    data = pd.concat(frames, ignore_index=True)
    data['date'] = pd.to_datetime(data.date)
    data = data[(data.date <= pd.Timestamp(args.as_of)) & data.series.isin(['EQ', 'BE', 'BZ', 'RR'])]
    if content is None:
        # Resolve identity on the exact requested session; no arbitrary symbol/ISIN guess.
        current = data[data.date == pd.Timestamp(args.as_of)]
        mapping = current.groupby('symbol')['isin'].agg(lambda s: sorted(set(s.dropna())))
        rows = []
        for symbol in universe.Symbol:
            ids = mapping.get(symbol, [])
            valid = [i for i in ids if isinstance(i,str) and re.fullmatch(r'IN[A-Z0-9]{10}',i)]
            # Keep every constituent in the denominator even if the price source
            # cannot resolve it. Unresolved symbols get no history.
            rows.append({'Company Name':symbol,'Industry':'Unknown','Symbol':symbol,
                         'Series':'EQ','ISIN Code':valid[0] if len(valid)==1 else ''})
        universe = pd.DataFrame(rows)
        dup = universe['ISIN Code'].ne('') & universe['ISIN Code'].duplicated(keep=False)
        universe.loc[dup,'ISIN Code'] = ''
        content = universe.to_csv(index=False).encode()
        source_meta['resolved_isins'] = int(universe['ISIN Code'].ne('').sum())
        source_meta.pop('symbols',None)
        # Reload older history for resolved ISINs under historical symbols.
        frames = []
        for year in range(2015,args.as_of.year+1):
            url = f'https://huggingface.co/datasets/{DATASET}/resolve/{revision}/prices_adjusted/nse_{year}.parquet'
            frame = pq.read_table(BytesIO(download(url)), columns=COLUMNS).to_pandas()
            ids = universe.loc[universe['ISIN Code'].ne(''),'ISIN Code']
            frames.append(frame[frame['isin'].isin(ids) | frame.symbol.isin(universe.Symbol)].copy())
        data = pd.concat(frames,ignore_index=True)
        data['date'] = pd.to_datetime(data.date)
        data = data[(data.date <= pd.Timestamp(args.as_of)) & data.series.isin(['EQ','BE','BZ','RR'])]
    spans = data.groupby(['symbol', 'isin']).date.agg(['min', 'max']).reset_index()
    by_isin = dict(tuple(data.groupby('isin')))
    prices = root / 'prices'
    prices.mkdir(exist_ok=True)
    with TemporaryDirectory(dir=root) as temp:
        stage = Path(temp)
        for symbol, isin in zip(universe.Symbol, universe['ISIN Code']):
            if not isin or isin not in by_isin:
                continue
            parts, used = [by_isin[isin]], {isin}
            # Include an earlier ISIN only for an unambiguous adjacent symbol history.
            while True:
                first = parts[-1].sort_values('date').iloc[0]
                prior = spans[(spans.symbol == first.symbol) & ~spans['isin'].isin(used)
                              & (spans['max'] < first.date)
                              & (spans['max'] >= first.date - pd.Timedelta(days=30))]
                if len(prior) != 1:
                    break
                previous = prior.iloc[0]['isin']
                parts.append(by_isin[previous].loc[lambda df: df.date < first.date])
                used.add(previous)
            history = pd.concat(parts).copy()
            history['priority'] = history.series.map({'EQ': 0, 'RR': 0, 'BE': 1, 'BZ': 2})
            history = history.sort_values(['date', 'priority', 'volume'], ascending=[True, True, False]).drop_duplicates('date')
            factor = history.adj_factor_cumulative
            if not (np.isfinite(factor) & (factor > 0)).all():
                raise ValueError(f'Invalid price adjustment for {symbol}')
            for field in ['open', 'high', 'low', 'close']:
                history[field] = history[field] * factor
            output = history[['date', 'open', 'high', 'low', 'close', 'volume']].copy()
            output.columns = ['Date', 'Open', 'High', 'Low', 'Close', 'Volume']
            output.to_csv(stage / f'{symbol}.NS.csv', index=False)
        # The workflow uses a fresh checkout. Never silently reuse stale inputs on a local retry.
        for symbol in universe.Symbol:
            src = stage / f'{symbol}.NS.csv'
            dst = prices / src.name
            if src.exists():
                src.replace(dst)
            elif dst.exists():
                raise ValueError(f'Existing CSV conflicts with missing input for {symbol}')
    universe_path.write_bytes(content)
    Path('output').mkdir(exist_ok=True)
    Path('output/market_source.json').write_text(json.dumps({
        'as_of': str(args.as_of), 'constituents': source, 'constituent_source_metadata': source_meta, 'dataset': DATASET,
        'revision': revision, 'basis': 'TejHQ cumulative adjustment applied to every OHLC field'
    }, indent=2))


if __name__ == '__main__':
    main()
