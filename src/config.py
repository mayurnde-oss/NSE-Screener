"""Validated settings and environment overrides."""
from __future__ import annotations
import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo
import yaml

@dataclass(frozen=True)
class Settings:
    root: Path
    timezone: str = 'Asia/Kolkata'
    universe_path: str = 'data/nifty500.csv'
    price_provider: str = 'yahoo_research'
    allow_yahoo_research: bool = False
    history_start: str = '2015-01-01'
    price_cache_dir: str = 'data/cache'
    full_refresh_days: int = 30
    batch_size: int = 12
    min_daily_rows: int = 420
    min_weekly_150_observations: int = 200
    min_weekly_observations: int = 250
    bb_period: int = 20
    bb_std: float = 1.5
    ema_periods: tuple[int, int] = (150, 200)
    strict_proximity_pct: float = 2.0
    near_proximity_pct: float = 5.0
    fundamental_score_min: float = 70.0
    fundamental_coverage_min: float = 70.0
    fundamentals_csv: str = 'data/fundamentals.csv'
    holiday_csv: str = 'data/holidays.csv'
    close_time_ist: str = '15:30'
    output_dir: str = 'output'
    sheets_enabled: bool = False
    telegram_enabled: bool = False

    def path(self, relative: str) -> Path:
        p = Path(relative)
        return p if p.is_absolute() else self.root / p

    def validate(self) -> None:
        ZoneInfo(self.timezone)
        if self.price_provider not in {'csv', 'yahoo_research'}:
            raise ValueError('price_provider must be csv or yahoo_research')
        if self.price_provider == 'yahoo_research' and not self.allow_yahoo_research:
            raise ValueError('Yahoo research adapter requires explicit allow_yahoo_research: true or ALLOW_YAHOO_RESEARCH=1. Check data usage rights first.')
        if not (0 <= self.strict_proximity_pct < self.near_proximity_pct <= 50):
            raise ValueError('Require 0 <= strict proximity < near proximity <= 50')
        if self.bb_period < 2 or self.bb_std <= 0 or sorted(self.ema_periods) != [150, 200]:
            raise ValueError('Unexpected BB parameters or weekly EMA periods')
        if (self.batch_size < 1 or self.min_weekly_150_observations < 150 or
                self.min_weekly_observations < 200 or self.min_weekly_observations < self.min_weekly_150_observations):
            raise ValueError('Invalid batch size or weekly warmup')
        if not (0 <= self.fundamental_score_min <= 100 and 0 <= self.fundamental_coverage_min <= 100):
            raise ValueError('Fundamental score/coverage must be in [0,100]')


def _env_boolean(name: str, fallback: bool) -> bool:
    val = os.getenv(name)
    return fallback if val is None else val.lower() in {'1', 'yes', 'true', 'on'}


def load_settings(file: Path, universe_override: str | None = None, source_override: str | None = None) -> Settings:
    file = file.resolve()
    with file.open('r', encoding='utf-8') as f:
        raw = yaml.safe_load(f) or {}
    allowed = set(Settings.__dataclass_fields__) - {'root'}
    unknown = set(raw) - allowed
    if unknown:
        raise ValueError(f'Unknown configuration keys: {sorted(unknown)}')
    raw['root'] = file.parent
    raw['ema_periods'] = tuple(raw.get('ema_periods', (150, 200)))
    raw['universe_path'] = universe_override or os.getenv('UNIVERSE_PATH', raw.get('universe_path', 'data/nifty500.csv'))
    raw['price_provider'] = source_override or os.getenv('PRICE_PROVIDER', raw.get('price_provider', 'yahoo_research'))
    raw['allow_yahoo_research'] = _env_boolean('ALLOW_YAHOO_RESEARCH', raw.get('allow_yahoo_research', False))
    raw['sheets_enabled'] = _env_boolean('SHEETS_ENABLED', raw.get('sheets_enabled', False))
    raw['telegram_enabled'] = _env_boolean('TELEGRAM_ENABLED', raw.get('telegram_enabled', False))
    s = Settings(**raw)
    s.validate()
    return s
