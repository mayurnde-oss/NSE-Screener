from datetime import date
import pandas as pd
from src.fundamentals import FundamentalCSV, score_fundamentals

D = date(2026,11,20)


def test_absent_fundamentals_cannot_qualify(tmp_path):
    source = FundamentalCSV(tmp_path/'not-exists.csv')
    res = score_fundamentals('TEST.NS','Technology',D,source,100.,100.)
    assert not res['fundamental_qualified']
    assert not res['fundamental_documented']
    assert res['fundamental_score'] <= 27


def test_future_published_statements_are_rejected(tmp_path):
    f = tmp_path/'f.csv'
    f.write_text('ticker,metric,value,period_end,published_at,source_url\n'
                 'TEST.NS,eps_q_yoy_pct,400,2026-09-30,2026-12-01,https://example.org/statement\n', encoding='utf-8')
    source = FundamentalCSV(f)
    assert 'eps_q_yoy_pct' not in source.documented_as_of('TEST.NS',D)


def test_sources_documented_per_metric_and_score(tmp_path):
    f = tmp_path/'f.csv'
    rows = ['ticker,metric,value,period_end,published_at,source_url']
    vals = {'eps_q_yoy_pct':40,'eps_annual_yoy_pct':32,'eps_accel_pp':5,
            'revenue_q_yoy_pct':35,'revenue_annual_yoy_pct':20,
            'roe_pct':28,'roce_pct':24,'operating_cash_flow_positive':1}
    for name,val in vals.items():
        rows.append(f'TEST.NS,{name},{val},2026-06-30,2026-08-15,https://example.org/{name}')
    f.write_text('\n'.join(rows)+'\n')
    source = FundamentalCSV(f)
    score = score_fundamentals('TEST.NS','Information Technology',D,source,95,95)
    assert score['fundamental_documented']
    assert score['fundamental_qualified']
    assert score['fundamental_score'] > 90
    assert score['fundamental_coverage_pct']==100
    assert 'example.org/eps_q_yoy_pct' in score['fundamental_sources']


def test_insurer_unverified_until_sector_rubric(tmp_path):
    source = FundamentalCSV(tmp_path/'missing.csv')
    score = score_fundamentals('INSUR.NS','Insurance',D,source,100,100)
    assert not score['fundamental_qualified']
    assert 'Insurance' in score['fundamental_notes']
