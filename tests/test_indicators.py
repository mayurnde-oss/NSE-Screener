import numpy as np
import pandas as pd
import pytest
from src.indicators import ema_sma_seed, add_indicators, range_to_ema_pct, classify_technical, evaluate_last, add_universe_ranks


def make_prices(periods=2600, end='2026-11-20'):
    idx = pd.bdate_range(end=end, periods=periods)
    close = pd.Series(np.linspace(100, 220, periods), index=idx)
    return pd.DataFrame({'Open':close*.999,'High':close*1.02,
                         'Low':close*.98,'Close':close,'Volume':1000000},index=idx)


def test_strict_high_equal_band_must_fail():
    assert classify_technical(99.,99.,1.5) == 'APPROACHING_SUPPORT'
    assert classify_technical(98.999,99.,1.5) == 'STRICT_MATCH'
    assert classify_technical(98.999,99.,2.5) == 'NEAR_MATCH'
    assert classify_technical(98.999,99.,5.01) == 'NO_MATCH'


def test_zero_range_distance_touch_cross_or_inside():
    assert range_to_ema_pct(97,103,100) == 0
    assert range_to_ema_pct(100,103,100) == 0
    assert range_to_ema_pct(97,100,100) == 0
    assert range_to_ema_pct(103,105,100) == 3
    assert range_to_ema_pct(95,97,100) == 3


def test_either_ema_qualifies():
    assert classify_technical(89.,90.,1.99) == 'STRICT_MATCH'  # 150-week near, 200-week irrelevant


def test_ema_is_sma_seed_then_weekly_recursive():
    s = pd.Series(np.arange(1,251,dtype=float))
    em = ema_sma_seed(s,200)
    assert em.iloc[:199].isna().all()
    assert em.iloc[199] == pytest.approx(np.arange(1,201).mean())
    assert em.iloc[200] == pytest.approx(em.iloc[199]*(1-2/201) + 201*2/201)


def test_weekly_emas_use_completed_week_not_daily_150_200():
    price = make_prices()
    data, weekly_count = add_indicators(price)
    assert weekly_count >= 490
    assert not np.isnan(data['WEMA200'].iloc[-1])
    assert data['WEMA200'].iloc[-1] != pytest.approx(data['SMA200'].iloc[-1], abs=.01)
    # Thursday week EMA is previous Friday's, Friday updated close is available.
    fri = data.index[-1]
    assert fri.weekday() == 4
    thur = data.index[-2]
    assert thur.weekday() == 3
    prev_friday = data.index[-6]
    assert prev_friday.weekday() == 4
    assert data.loc[thur,'WEMA200'] == pytest.approx(data.loc[prev_friday,'WEMA200'])
    assert data.loc[fri,'WEMA200'] != data.loc[prev_friday,'WEMA200']


def test_weekly_no_look_ahead_price_change_friday():
    original = make_prices()
    modified = original.copy()
    # Alter final Friday close but Thursday EMA must NOT be affected.
    modified.loc[modified.index[-1],'Close'] *= 1.15
    modified.loc[modified.index[-1],'High'] *= 1.2
    before, _ = add_indicators(original)
    after, _ = add_indicators(modified)
    assert before['WEMA200'].iloc[-2] == pytest.approx(after['WEMA200'].iloc[-2])
    assert before['WEMA200'].iloc[-1] != pytest.approx(after['WEMA200'].iloc[-1])


def test_rank_full_universe_and_template_diagnostic():
    r = [{'ticker':'A','momentum_factor':.2,'industry':'Tech','trend_template_passes_of_8':4},
         {'ticker':'B','momentum_factor':.1,'industry':'Tech','trend_template_passes_of_8':3}]
    add_universe_ranks(r)
    assert r[0]['rs_percentile'] == 100.0
    assert r[1]['rs_percentile'] == 50.0
    assert r[0]['trend_template_passes_of_8'] == 5
    assert r[0]['industry_rs_percentile'] is None  # <5 group members


def test_missing_history_not_strict():
    short, weeks = add_indicators(make_prices(250))
    result = evaluate_last(short, 'DEMO.NS', 2.,5.)
    assert result['technical_bucket']=='INSUFFICIENT_DATA'
