from pathlib import Path
import json
import pandas as pd
import pytest
from src.constituent_sources import validate, fresh, resolve_universe

def sample():
    return ('Company Name,Industry,Symbol,Series,ISIN Code\n' + ''.join(
        f'Company {i},Industry,S{i},EQ,IN{i:010d}\n' for i in range(500))).encode()

def test_valid_universe():
    assert len(validate(sample()).splitlines()) == 501

def test_duplicate_rejected():
    with pytest.raises(ValueError):
        validate(sample() + sample().splitlines()[1] + b'\n')

def test_stale_rejected():
    with pytest.raises(ValueError):
        fresh('2026-01-01T00:00:00Z', pd.Timestamp('2026-02-01T00:00:00Z'))

def test_cache_preserves_source_date(tmp_path, monkeypatch):
    from src import constituent_sources as c
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv('NIFTY500_CSV_URL', raising=False)
    monkeypatch.setenv('NIFTY500_LOCAL_AS_OF','2026-11-20T00:00:00Z')
    root = Path('data')
    root.mkdir()
    (root/'nifty500.csv').write_bytes(sample())
    def unavailable(url):
        raise OSError('offline')
    monkeypatch.setattr(c,'fetch',unavailable)
    now = pd.Timestamp('2026-11-27T00:00:00Z')
    resolve_universe(root, now)
    (root/'nifty500.csv').unlink()
    _, _, meta = resolve_universe(root, now)
    assert meta['fallback'] and meta['retrieved'].startswith('2026-11-20')
    with pytest.raises(RuntimeError):
        resolve_universe(root,pd.Timestamp('2026-12-20T00:00:00Z'))
