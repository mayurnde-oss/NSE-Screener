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
    # A privately supplied constituent file overrides the published CSV download.
    content = universe_path.read_bytes() if universe_path.exists() else download(CONSTITUENTS)
    universe = pd.read_csv(BytesIO(content), dtype=str, keep_default_na=False)
    if not {'Symbol', 'Company Name', 'Industry', 'Series', 'ISIN Code'} <= set(universe.columns):
        raise ValueError('Invalid Nifty 500 constituent file')
    if not 490 <= universe['ISIN Code'].nunique() <= 520:
        raise ValueError('Incomplete or unexpected Nifty 500 membership')
    if not universe.Symbol.map(lambda s: bool(SAFE_TICKER.fullmatch(s + '.NS'))).all():
        raise ValueError('Unexpected ticker in constituent file')
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
    spans = data.groupby(['symbol', 'isin']).date.agg(['min', 'max']).reset_index()
    by_isin = dict(tuple(data.groupby('isin')))
    prices = root / 'prices'
    prices.mkdir(exist_ok=True)
    with TemporaryDirectory(dir=root) as temp:
        stage = Path(temp)
        for symbol, isin in zip(universe.Symbol, universe['ISIN Code']):
            if isin not in by_isin:
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
    if not universe_path.exists():
        universe_path.write_bytes(content)
    Path('output').mkdir(exist_ok=True)
    Path('output/market_source.json').write_text(json.dumps({
        'as_of': str(args.as_of), 'constituents': CONSTITUENTS, 'dataset': DATASET,
        'revision': revision, 'basis': 'TejHQ cumulative adjustment applied to every OHLC field'
    }, indent=2))


if __name__ == '__main__':
    main()
