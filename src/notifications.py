"""Telegram extension point (disabled in this initial release).

Do not send any alerts until Phase 6 opt-in is designed and tested. Signal
states are exported to Sheets and can be consumed later by an alert service.
"""
from __future__ import annotations

def send_notifications(*args, **kwargs):
    raise NotImplementedError('Telegram alerts are intentionally disabled until Phase 6')
