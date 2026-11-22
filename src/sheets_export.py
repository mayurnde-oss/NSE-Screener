"""Google Sheets output; state/history never cleared, Settings user-editable."""
from __future__ import annotations
import json
import logging
import os
from typing import Any
from .config import Settings

LOG = logging.getLogger(__name__)
SHEET_NAMES = (
    'Dashboard', 'Strict Matches', 'Technical Matches - Unverified', 'Technical Matches - Below Score', 'Near Matches',
    'Watchlist', 'Fundamental Analysis', 'Signal History', 'Settings', 'Scan Logs',
)
HEADERS = [
    'Signal Date', 'Ticker', 'Company', 'Exchange', 'Industry', 'Signal Status', 'Technical Bucket',
    'Close (INR)', 'Daily High (INR)', 'Daily Low (INR)', 'Lower BB (INR)',
    'EMA 150 Weekly (INR)', 'EMA 200 Weekly (INR)', 'Nearest Support',
    'Range to Support (%)', 'Close to EMA150 (%)', 'Close to EMA200 (%)',
    'Fundamental Score / 100', 'Fundamental Coverage (%)', 'RS Percentile',
    'EPS YoY (%)', 'Revenue YoY (%)', 'ROE (%)', 'ROCE (%)',
    'Trend Template / 8', 'Source Publication Date', 'Financial Evidence URLs', 'Data Provider', 'Data Quality Note'
]
HISTORY_HEADERS = ['signal_date','ticker','status','previous_status','support_ema',
                   'support_ema_value','close','setup_high','notes']
LOG_HEADERS = ['Scan time IST','As-of date','Result','Universe','Fresh','Failed','Strict qualified',
               'Technical unverified','Below score','Near','Approaching','Message']
SETTINGS_ROWS = [
    ['Parameter', 'Value', 'Description'],
    ['strict_proximity_pct','2.0','Daily range-to-weekly EMA distance ≤2%'],
    ['near_proximity_pct','5.0','Wider watchlist distance ≤5%'],
    ['bb_period','20','Daily Bollinger Band SMA window'],
    ['bb_std','1.5','Daily Bollinger Band multiplier; ddof=0'],
    ['fundamental_score_min','70','Score threshold /100'],
    ['fundamental_coverage_min','70','Minimum documented score coverage /100'],
]


def cells_for_record(r: dict) -> list:
    return [r.get('signal_date'), r.get('ticker'),r.get('company'),r.get('exchange'),r.get('industry'),
            r.get('signal_status'),r.get('technical_bucket'),r.get('close'),r.get('daily_high'),
            r.get('daily_low'),r.get('bb_lower'),r.get('wema150'),r.get('wema200'),
            r.get('nearest_ema'),r.get('distance_pct'),r.get('close_to_ema150_pct'),
            r.get('close_to_ema200_pct'),r.get('fundamental_score'),r.get('fundamental_coverage_pct'),
            r.get('rs_percentile'),r.get('eps_q_yoy_pct'),r.get('revenue_q_yoy_pct'),r.get('roe_pct'),
            r.get('roce_pct'),r.get('trend_template_passes_of_8'),r.get('fundamental_last_published'),
            r.get('fundamental_sources'),r.get('data_provider'),r.get('data_quality_note')]


class GoogleSheetExporter:
    def __init__(self, settings: Settings):
        from google.oauth2.service_account import Credentials
        import gspread
        raw = os.getenv('GOOGLE_SERVICE_ACCOUNT_JSON','').strip()
        sid = os.getenv('GOOGLE_SHEET_ID','').strip()
        if not raw or not sid:
            raise ValueError('Sheets enabled: set GOOGLE_SERVICE_ACCOUNT_JSON and GOOGLE_SHEET_ID (GitHub Secrets)')
        info = json.loads(raw)
        credentials = Credentials.from_service_account_info(info, scopes=['https://www.googleapis.com/auth/spreadsheets'])
        self.doc = gspread.authorize(credentials).open_by_key(sid)
        self.sheet = {}
        existing = {ws.title: ws for ws in self.doc.worksheets()}
        for name in SHEET_NAMES:
            if name in existing:
                self.sheet[name] = existing[name]
            else:
                ws = self.doc.add_worksheet(title=name, rows=600, cols=max(len(HEADERS), 30))
                self.sheet[name] = ws
                try:
                    ws.freeze(rows=1)
                    ws.format('A1:AB1', {'backgroundColor':{'red':0.08,'green':0.16,'blue':0.28},
                                         'textFormat':{'bold':True,'foregroundColor':{'red':1,'green':1,'blue':1}}})
                except Exception as exc:
                    LOG.warning('Noncritical sheet formatting failed: %s', exc)
        if not self.sheet['Settings'].get_all_values():
            self.sheet['Settings'].update(range_name='A1:C7', values=SETTINGS_ROWS, value_input_option='RAW')

    def overrides(self) -> dict[str, float]:
        vals = self.sheet['Settings'].get_all_values()
        permitted = {'strict_proximity_pct','near_proximity_pct','bb_period', 'bb_std',
                     'fundamental_score_min','fundamental_coverage_min'}
        data = {}
        for row in vals[1:]:
            if len(row) < 2 or row[0] not in permitted:
                continue
            try:
                data[row[0]] = float(row[1])
            except ValueError:
                raise ValueError(f'Invalid Settings value for {row[0]}')
        strict = data.get('strict_proximity_pct',2)
        near = data.get('near_proximity_pct',5)
        if not (0 <= strict < near <= 50):
            raise ValueError('Sheets Settings: strict proximity must be < near proximity')
        if data.get('bb_period',20) < 2 or data.get('bb_std',1.5) <= 0:
            raise ValueError('Sheets Settings: BB parameters invalid')
        return data

    def history(self) -> list[dict]:
        rows = self.sheet['Signal History'].get_all_values()
        if not rows:
            return []
        if rows[0] != HISTORY_HEADERS:
            raise ValueError('Signal History headers modified; refusing to overwrite history')
        return [dict(zip(HISTORY_HEADERS, row)) for row in rows[1:] if len(row)>1]

    def _replace(self, sheet_name: str, values: list[list]) -> None:
        ws = self.sheet[sheet_name]
        ws.clear()
        if values:
            ws.update(range_name='A1', values=[[x if x is not None else '' for x in row] for row in values], value_input_option='RAW')
        try:
            ws.freeze(rows=1)
        except Exception:
            pass

    def record_failure(self, as_of: str, message: str, universe: int, fresh: int, run_time: str):
        """Append a failure entry without clearing the last successful dashboard."""
        ws = self.sheet['Scan Logs']
        if not ws.get_all_values():
            ws.update(range_name='A1', values=[LOG_HEADERS], value_input_option='RAW')
        ws.append_row([run_time, as_of, 'FAILED', universe, fresh, universe-fresh,
                       '', '', '', '', '', message[:450]], value_input_option='RAW', table_range='A:L')

    def publish(self, results: list[dict], new_events: list[dict], summary: dict):
        qualified = [r for r in results if r['signal_status']=='STRICT QUALIFIED SETUP']
        unverified = [r for r in results if r['signal_status']=='TECHNICAL MATCH - FUNDAMENTALS UNVERIFIED']
        below_score = [r for r in results if r['signal_status']=='TECHNICAL MATCH - FUNDAMENTALS BELOW SCORE']
        near = [r for r in results if r['technical_bucket'] in {'NEAR_MATCH','APPROACHING_SUPPORT'}]
        watchlist = [r for r in results if r['signal_status'] not in {'NO SETUP'}]
        sort_key = lambda r: (-(r.get('fundamental_score') or 0),r.get('distance_pct') if r.get('distance_pct') is not None else 999)
        tabs = {
            'Strict Matches': qualified,
            'Technical Matches - Unverified': unverified,
            'Technical Matches - Below Score': below_score,
            'Near Matches': near,
            'Watchlist': watchlist,
            'Fundamental Analysis': [r for r in results if r['technical_bucket']=='STRICT_MATCH'],
        }
        for name, rs in tabs.items():
            self._replace(name, [HEADERS]+[cells_for_record(r) for r in sorted(rs,key=sort_key)])
        dashboard = [['Metric','Value']]+[[key,str(value)] for key,value in summary.items()]
        dashboard += [['Notes','Signal-only research; incomplete fundamentals remain unverified'],
                      ['Definitions','Strict: Daily High < lower BB, range-to-EMA ≤2%'],
                      ['Warning','Zero matches can be the correct result; do not weaken filters silently']]
        self._replace('Dashboard', dashboard)
        if new_events:
            ws = self.sheet['Signal History']
            if not ws.get_all_values():
                ws.update(range_name='A1',values=[HISTORY_HEADERS],value_input_option='RAW')
            ws.append_rows([[ev.get(k,'') for k in HISTORY_HEADERS] for ev in new_events], value_input_option='RAW')
        self.sheet['Scan Logs'].append_row([
            summary.get('run_time_ist',''), summary.get('as_of',''),'SUCCESS',
            summary.get('universe',0),summary.get('fresh',0),summary.get('failed',0),
            summary.get('qualified',0),summary.get('unverified',0),summary.get('below_score',0), summary.get('near',0),
            summary.get('approaching',0),'See dashboard',
        ], value_input_option='RAW', table_range='A:L')
        if self.sheet['Scan Logs'].get('A1') != [LOG_HEADERS]:
            # Do not overwrite existing log rows; the first row is a header only on first run.
            if len(self.sheet['Scan Logs'].get_all_values()) == 1:
                self.sheet['Scan Logs'].insert_row(LOG_HEADERS, index=1)
