"""Durable signal transitions and duplicate suppression."""
from __future__ import annotations
from datetime import date

SETUP_STATES = {'STRICT QUALIFIED SETUP', 'TECHNICAL MATCH - FUNDAMENTALS UNVERIFIED',
                'TECHNICAL MATCH - FUNDAMENTALS BELOW SCORE',
                'REBOUND DEVELOPING'}


def base_status(row: dict) -> str:
    t = row['technical_bucket']
    if t == 'STRICT_MATCH':
        if row.get('fundamental_qualified', False):
            return 'STRICT QUALIFIED SETUP'
        if row.get('fundamental_documented', False):
            return 'TECHNICAL MATCH - FUNDAMENTALS BELOW SCORE'
        return 'TECHNICAL MATCH - FUNDAMENTALS UNVERIFIED'
    if t == 'NEAR_MATCH':
        return 'NEAR MATCH'
    if t == 'APPROACHING_SUPPORT':
        return 'WATCHLIST - APPROACHING SUPPORT'
    return 'NO SETUP'


def _number(x, default=None):
    try:
        if x is None or str(x).strip() == '':
            return default
        return float(x)
    except (ValueError, TypeError):
        return default


def history_key(event: dict) -> tuple:
    return (str(event.get('signal_date','')), str(event.get('ticker','')),
            str(event.get('status','')))


def make_events(records: list[dict], history: list[dict]) -> tuple[list[dict], list[dict]]:
    """Status transitions are evaluated once per latest completed daily date.

    If a previous strict setup exists, confirmation is EITHER (close above previous
    completed daily high AND above previous daily close) OR (close above lower
    BB AND above previous daily close). Invalidation: close 5% below original support.
    These are research defaults, not executable trade orders.
    """
    prior = {}
    for h in sorted(history, key=lambda h: (str(h.get('signal_date','')), str(h.get('ticker','')))):
        prior[str(h.get('ticker',''))] = h
    present_keys = {history_key(h) for h in history}
    new_events = []
    watchlist = []
    for row in records:
        ticker = row['ticker']
        current = base_status(row)
        last = prior.get(ticker)
        last_status = str(last.get('status')) if last else ''
        prev_date = str(last.get('signal_date')) if last else ''
        if last and prev_date < row['signal_date'] and last_status in SETUP_STATES:
            last_support = _number(last.get('support_ema_value'))
            initial_high = _number(last.get('setup_high'))
            prev_close = _number(last.get('close'))
            closing = float(row['close'])
            day_before_close = _number(row.get('previous_daily_close'), prev_close)
            day_before_high = _number(row.get('previous_daily_high'), initial_high)
            above_prior_close = day_before_close is not None and closing > day_before_close
            if last_support and closing < last_support*0.95:
                current = 'INVALIDATED'
            elif above_prior_close and ((day_before_high is not None and closing > day_before_high) or
                                         (row.get('bb_lower') is not None and closing > row['bb_lower'])):
                current = 'REBOUND CONFIRMED'
            elif above_prior_close:
                current = 'REBOUND DEVELOPING'
            elif row['technical_bucket'] == 'STRICT_MATCH':
                current = last_status  # no duplicate event if setup persists
        row['signal_status'] = current
        if current not in {'NO SETUP'}:
            watchlist.append(row)
        if current not in {'STRICT QUALIFIED SETUP', 'TECHNICAL MATCH - FUNDAMENTALS UNVERIFIED',
                           'TECHNICAL MATCH - FUNDAMENTALS BELOW SCORE', 'REBOUND CONFIRMED', 'REBOUND DEVELOPING', 'INVALIDATED'}:
            continue
        if last and last_status == current:
            continue
        event = {
            'signal_date': row['signal_date'], 'ticker': ticker, 'status': current,
            'previous_status': last_status, 'support_ema': row.get('nearest_ema',''),
            'support_ema_value': row.get('wema150') if row.get('nearest_ema')=='WEMA150' else row.get('wema200'),
            'close': row['close'], 'setup_high': row['daily_high'],
            'notes': 'Research signal only; no order recommendation',
        }
        # A developing rebound retains original support and high for future days.
        if last and last_status in SETUP_STATES:
            event['support_ema'] = last.get('support_ema')
            event['support_ema_value'] = last.get('support_ema_value')
            event['setup_high'] = last.get('setup_high')
        k = history_key(event)
        if k not in present_keys:
            new_events.append(event)
            present_keys.add(k)
            prior[ticker] = event
    return new_events, watchlist
