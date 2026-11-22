"""Offline end-to-end acceptance, no market API or Google credentials needed."""
import csv
from pathlib import Path
import numpy as np
import pandas as pd
from src.cli import run


def make_constant_prices(last_drop: bool, dates: pd.DatetimeIndex) -> pd.DataFrame:
    n = len(dates)
    close = np.full(n,100.0)
    high = np.full(n,100.5)
    low = np.full(n,99.5)
    opening = np.full(n,100.0)
    if last_drop:
        close[-1], high[-1], low[-1], opening[-1] = 98.0,98.3,97.8,98.1
    return pd.DataFrame({'Date':dates.strftime('%Y-%m-%d'), 'Open':opening,
                         'High':high,'Low':low,'Close':close,
                         'Volume':np.full(n,1_000_000)})


def test_strict_candidate_remains_unverified_and_history_idempotent(tmp_path):
    data=tmp_path/'data'
    (data/'prices').mkdir(parents=True)
    dates=pd.bdate_range(end='2026-11-19',periods=2600)
    make_constant_prices(True,dates).to_csv(data/'prices/TEST1.NS.csv',index=False)
    make_constant_prices(False,dates).to_csv(data/'prices/TEST2.NS.csv',index=False)
    (data/'universe.csv').write_text('Symbol,Company Name,Industry\nTEST1,Test First,Technology\nTEST2,Test Second,Technology\n')
    (data/'fundamentals.csv').write_text('ticker,metric,value,period_end,published_at,source_url\n')
    config=tmp_path/'config.yaml'
    config.write_text('price_provider: csv\nuniverse_path: data/universe.csv\n'
                      'fundamentals_csv: data/fundamentals.csv\n'
                      'history_start: "2015-01-01"\n')
    argv=['--config',str(config),'--as-of','2026-11-19','--no-sheets']
    assert run(argv)==0
    with (tmp_path/'output/technical_unverified.csv').open() as fh:
        matches=list(csv.DictReader(fh))
    assert [m['Ticker'] for m in matches]==['TEST1.NS']
    assert matches[0]['Technical Bucket']=='STRICT_MATCH'
    assert matches[0]['Lower BB (INR)']!=''
    with (tmp_path/'output/strict_qualified.csv').open() as fh:
        assert len(list(csv.DictReader(fh)))==0
    hist=data/'state/signal_history.csv'
    assert len(list(csv.DictReader(hist.open())))==1
    assert run(argv)==0
    assert len(list(csv.DictReader(hist.open())))==1
