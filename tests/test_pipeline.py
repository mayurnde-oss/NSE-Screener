import csv
from datetime import date
import pandas as pd
import pytest
from src.price_data import normalize_prices
from src.signal_engine import make_events
from src.universe import load_universe
from src.cli import holiday_name


def test_ohlcv_invalid_dropped():
    idx = pd.date_range('2026-11-19',periods=2)
    df = pd.DataFrame({'Open':[10,10],'High':[12,9],
                       'Low':[9,8],'Close':[11,10],'Volume':[100,200]},index=idx)
    norm = normalize_prices(df)
    assert len(norm)==1


def test_constituents_dedupe_isin_prioritizes_first_listing(tmp_path):
    p = tmp_path/'universe.csv'
    p.write_text('Symbol,Company Name,Exchange,ISIN Code\nAAA,Alpha,NSE,INE000000111\nAAA,Beta,BSE,INE000000111\nBBB,Gamma,BSE,\n')
    rows = load_universe(p)
    assert [r.ticker for r in rows] == ['AAA.NS','BBB.BO']


def test_prevent_duplicate_signal_date(tmp_path):
    record = {'ticker':'T.NS','signal_date':'2026-11-20','technical_bucket':'STRICT_MATCH',
              'fundamental_qualified':False,'nearest_ema':'WEMA150','wema150':100.,
              'close':100,'daily_high':101,'bb_lower':102}
    first, _ = make_events([record.copy()],[])
    assert len(first)==1
    second, _ = make_events([record.copy()],first)
    assert second==[]


def test_rebound_and_invalidation_are_distinct():
    history = [{'ticker':'T.NS','signal_date':'2026-11-19',
                'status':'TECHNICAL MATCH - FUNDAMENTALS UNVERIFIED',
                'support_ema':'WEMA150','support_ema_value':100,
                'setup_high':99,'close':96}]
    up = {'ticker':'T.NS','signal_date':'2026-11-20','technical_bucket':'NO_MATCH',
          'fundamental_qualified':False,'close':101,'daily_high':101.5,'bb_lower':99,
          'nearest_ema':'WEMA150','wema150':100}
    evt, _ = make_events([up],history)
    assert len(evt)==1 and evt[0]['status']=='REBOUND CONFIRMED'
    down = dict(up, close=93)
    evt2, _ = make_events([down],history)
    assert len(evt2)==1 and evt2[0]['status']=='INVALIDATED'


def test_market_holiday_manual_calendar(tmp_path):
    f = tmp_path/'holidays.csv'
    f.write_text('date,name\n2026-11-09,Festival\n')
    assert holiday_name(f,date(2026,11,9))=='Festival'
    assert holiday_name(f,date(2026,11,20)) is None
