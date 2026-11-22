"""Load a manually supplied index constituent file. Never scrape exchange sites."""
from dataclasses import dataclass
from pathlib import Path
import pandas as pd

@dataclass(frozen=True)
class Instrument:
    ticker: str
    symbol: str
    company: str
    industry: str
    isin: str
    exchange: str


def load_universe(path: Path, max_stocks: int | None = None) -> list[Instrument]:
    if not path.exists():
        raise FileNotFoundError(f'Missing universe CSV: {path}. Download it manually from the official NIFTY 500 page; see README.')
    df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding='utf-8-sig')
    renames = {c: c.strip().lower().replace('_', ' ') for c in df.columns}
    df = df.rename(columns=renames)
    if 'symbol' not in df:
        raise ValueError('Universe CSV must contain Symbol column')
    seen: set[str] = set()
    result = []
    for _, row in df.iterrows():
        symbol = str(row.get('symbol', '')).strip().upper()
        if not symbol or symbol in {'SYMBOL', 'NAN'}:
            continue
        exchange = str(row.get('exchange', 'NSE')).strip().upper() or 'NSE'
        if exchange not in {'NSE', 'BSE'}:
            raise ValueError(f'Invalid exchange {exchange} for {symbol}')
        ticker = symbol if symbol.endswith(('.NS','.BO')) else symbol + ('.NS' if exchange == 'NSE' else '.BO')
        isin = str(row.get('isin code', row.get('isin', ''))).strip().upper()
        key = isin if isin else ticker
        if key in seen:
            continue
        seen.add(key)
        result.append(Instrument(ticker, symbol, str(row.get('company name', row.get('company',symbol))),
                                 str(row.get('industry', 'Unknown')), isin, exchange))
    if not result:
        raise ValueError('Universe CSV contains no valid symbols')
    return result[:max_stocks] if max_stocks else result
