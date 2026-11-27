"""Validated NIFTY 500 membership, with bounded cache and no universe substitution."""
import hashlib
from io import BytesIO
import json
from pathlib import Path
from urllib.request import urlopen
import pandas as pd
from .price_data import SAFE_TICKER

API = 'https://api.dharun.dev/v1/indices/nifty-500'
# Optional additional provider: operator supplies a permitted HTTPS CSV URL and
# its independently verified snapshot date. No scraping or access-block bypass.
TRACKER = 'https://raw.githubusercontent.com/floyds1995/Auto-Index-Constituents-Tracker/main/NSE/Nifty_500.csv'
CACHE_MAX_DAYS = 21
COLS = ['Company Name', 'Industry', 'Symbol', 'Series', 'ISIN Code']

def validate(content):
    frame = pd.read_csv(BytesIO(content), dtype=str, keep_default_na=False)
    if not set(COLS) <= set(frame.columns):
        raise ValueError('Missing official constituent columns')
    frame = frame[COLS].apply(lambda c: c.str.strip())
    if not 490 <= len(frame) <= 520 or (frame == '').any().any():
        raise ValueError('Incomplete NIFTY 500 membership')
    if frame.Symbol.duplicated().any() or frame['ISIN Code'].duplicated().any():
        raise ValueError('Duplicate symbol or ISIN')
    if not frame['ISIN Code'].str.fullmatch(r'IN[A-Z0-9]{10}').all():
        raise ValueError('Invalid ISIN')
    if not frame.Symbol.map(lambda s: bool(SAFE_TICKER.fullmatch(s + '.NS'))).all():
        raise ValueError('Invalid symbol')
    return frame.to_csv(index=False).encode()

def fresh(stamp, now):
    ts = pd.Timestamp(stamp)
    if pd.isna(ts) or ts.tzinfo is None:
        raise ValueError('Missing timezone-aware source timestamp')
    age = now - ts
    if not pd.Timedelta(0) <= age <= pd.Timedelta(days=CACHE_MAX_DAYS):
        raise ValueError(f'Expired or future constituent snapshot: {ts}')
    return ts.isoformat()

def fetch(url):
    from urllib.error import HTTPError
    for attempt in range(2):
        try:
            with urlopen(url, timeout=25) as response:
                raw = response.read(4_000_001)
                if len(raw) > 4_000_000:
                    raise ValueError('Constituent response too large')
                return raw
        except HTTPError as exc:
            if exc.code in (401, 403, 404) or attempt:
                raise
        except OSError:
            if attempt:
                raise

def from_api(raw, now):
    payload = json.loads(raw)
    index = payload['data']
    members = index['members']
    if index['id'] != 'nifty-500' or index['count'] != len(members):
        raise ValueError('Unexpected index API response')
    meta = payload['meta']['source']
    stamp = fresh(meta['retrieved'], now)
    rows = [{c: m[k] for c,k in zip(COLS, ['company','industry','symbol','series','isin'])}
            for m in members]
    return validate(pd.DataFrame(rows).to_csv(index=False).encode()), stamp

def tracker_members(raw, now):
    frame = pd.read_csv(BytesIO(raw), dtype=str, keep_default_na=False)
    dates = pd.to_datetime(frame['date'], format='%m/%d/%Y', errors='raise', utc=True)
    idx = dates.idxmax()
    stamp = fresh(dates.loc[idx], now)
    symbols = [s.strip() for s in frame.loc[idx, 'tickers'].split(',')]
    if (not 490 <= len(symbols) <= 520 or len(set(symbols)) != len(symbols)
            or not all(SAFE_TICKER.fullmatch(s + '.NS') for s in symbols)):
        raise ValueError('Invalid tracked NIFTY 500 membership')
    return symbols, stamp

def resolve_universe(root, now=None):
    import os
    now = pd.Timestamp.now(tz='UTC') if now is None else now
    cache = root / 'cache' / 'nifty500_snapshot.json'
    out = Path('output')
    out.mkdir(exist_ok=True)
    failures = []
    def save(content, source, stamp):
        meta = {'source': source, 'retrieved': stamp, 'sha256': hashlib.sha256(content).hexdigest()}
        cache.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache.with_suffix('.tmp')
        tmp.write_text(json.dumps({**meta, 'csv': content.decode()}))
        tmp.replace(cache)
        return content, source, {**meta, 'fallback': False, 'attempt_errors': failures}
    # Prefer a fresh explicitly authorized provider when configured.
    url = os.getenv('NIFTY500_CSV_URL','')
    if url:
        try:
            if not url.startswith('https://'):
                raise ValueError('HTTPS required')
            stamp = fresh(os.environ['NIFTY500_CSV_AS_OF'], now)
            return save(validate(fetch(url)), 'configured HTTPS CSV', stamp)
        except Exception as exc:
            failures.append({'source':'configured HTTPS CSV','error':type(exc).__name__})
    try:
        content, stamp = from_api(fetch(API), now)
        return save(content, API, stamp)
    except Exception as exc:
        failures.append({'source':API,'error':str(exc)})
    # Refresh the independent tracker every run, not merely after cache expiry.
    try:
        raw = fetch(TRACKER)
        symbols, stamp = tracker_members(raw, now)
        meta = {'source':TRACKER,'retrieved':stamp,'kind':'tracker',
                'sha256':hashlib.sha256(raw).hexdigest()}
        cache.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache.with_suffix('.tmp')
        tmp.write_text(json.dumps({**meta,'csv':raw.decode()}))
        tmp.replace(cache)
        return None, TRACKER, {'retrieved':stamp,'fallback':True,
                               'symbols':symbols,'attempt_errors':failures,
                               'industry_status':'unverified'}
    except Exception as exc:
        failures.append({'source':TRACKER,'error':str(exc)})
    try:
        obj = json.loads(cache.read_text())
        stamp = fresh(obj['retrieved'], now)
        raw = obj['csv'].encode()
        if hashlib.sha256(raw).hexdigest() != obj['sha256']:
            raise ValueError('Cache checksum mismatch')
        if obj.get('kind') == 'tracker':
            symbols, original_stamp = tracker_members(raw, now)
            if original_stamp != stamp:
                raise ValueError('Cache source timestamp mismatch')
            return None, TRACKER, {'retrieved':stamp,'fallback':True,
                'symbols':symbols,'attempt_errors':failures,'industry_status':'unverified',
                'cached':True}
        return validate(raw), obj['source'], {'retrieved':stamp,'fallback':True,'attempt_errors':failures}
    except Exception as exc:
        failures.append({'source':'validated cache','error':str(exc)})
    # Explicit dated local snapshot is a recovery option, not an automatic refresh.
    try:
        stamp = fresh(os.environ['NIFTY500_LOCAL_AS_OF'], now)
        return save(validate((root/'nifty500.csv').read_bytes()), 'dated local snapshot', stamp)
    except Exception as exc:
        failures.append({'source':'dated local snapshot','error':type(exc).__name__})
    (out/'universe_failure.json').write_text(json.dumps(failures, indent=2))
    raise RuntimeError('No fresh verified NIFTY 500 membership; see output/universe_failure.json')
