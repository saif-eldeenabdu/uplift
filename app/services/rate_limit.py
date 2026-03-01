"""
app/services/rate_limit.py
─────────────────────────────────────────────────────────────────
In-memory sliding-window rate limiter.
Two dimensions:
  • per-user  (max N submissions per calendar day)
  • per-IP    (max M submissions per rolling hour)

Uses simple dicts; resets naturally on restart.
"""

from __future__ import annotations

import time
from collections import defaultdict
from datetime import date
from typing import Optional

from app.settings import settings

# ── Storage ─────────────────────────────────────────────────────
# user_id → {date_str: count}
_user_daily: dict[str, dict[str, int]] = defaultdict(dict)

# ip → list of unix timestamps
_ip_hourly: dict[str, list[float]] = defaultdict(list)


def check_rate_limit(user_id: str, ip: str) -> Optional[str]:
    """
    Return a human-readable reason if the request should be blocked,
    or None if it is within limits.
    """
    # ── Per-user daily limit ────────────────────────────────────
    today_str = str(date.today())
    user_today = _user_daily[user_id].get(today_str, 0)
    if user_today >= settings.max_submissions_per_user_per_day:
        return (
            f"You've already submitted {user_today} "
            f"message{'s' if user_today != 1 else ''} today. "
            f"Come back tomorrow!"
        )

    # ── Per-IP hourly limit ─────────────────────────────────────
    now = time.time()
    one_hour_ago = now - 3600
    # Prune old timestamps
    _ip_hourly[ip] = [t for t in _ip_hourly[ip] if t > one_hour_ago]
    if len(_ip_hourly[ip]) >= settings.max_submissions_per_ip_per_hour:
        return "Too many submissions from this network. Please wait a while."

    return None


def record_submission(user_id: str, ip: str) -> None:
    """Call AFTER a successful submission to update counters."""
    today_str = str(date.today())
    _user_daily[user_id][today_str] = _user_daily[user_id].get(today_str, 0) + 1
    _ip_hourly[ip].append(time.time())


def reset() -> None:
    """Clear all counters (useful in tests)."""
    _user_daily.clear()
    _ip_hourly.clear()
