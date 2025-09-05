from __future__ import annotations
from datetime import datetime, timezone
import os
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def overlaps(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    return a_start < b_end and b_start < a_end


def _resolve_local_zone() -> ZoneInfo | timezone:
    """Resolve a reasonable local timezone.

    Preference order:
    - APP_TZ env var (IANA name)
    - TZ env var (IANA name; ignores values like 'Local')
    - System localtime (/etc/localtime)
    - America/Los_Angeles (common default for this app)
    - UTC
    """
    # 1) explicit app setting
    for key in ("APP_TZ", "TZ"):
        tzname = os.getenv(key)
        if tzname and tzname.lower() != "local":
            try:
                return ZoneInfo(tzname)
            except ZoneInfoNotFoundError:
                pass

    # 2) system localtime
    try:
        with open("/etc/localtime", "rb") as f:
            return ZoneInfo.from_file(f)
    except Exception:
        pass

    # 3) opinionated fallback to LA (safe for this project), then UTC
    for name in ("America/Los_Angeles", "UTC"):
        try:
            return ZoneInfo(name)
        except Exception:
            continue
    return timezone.utc


# Cached local tz for conversions
LOCAL_TZ = _resolve_local_zone()


def parse_to_utc_naive(value: str) -> datetime:
    """Parse an ISO-8601 string (with Z, offset, or naive) and return UTC as naive datetime.

    - If string has a timezone offset or Z, convert to UTC and drop tzinfo.
    - If string is naive, assume LOCAL_TZ, convert to UTC and drop tzinfo.
    """
    s = (value or "").strip()
    if not s:
        raise ValueError("Empty datetime string")
    # Support trailing Z
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=LOCAL_TZ)
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def isoformat_z(dt: datetime) -> str:
    """Format a naive UTC datetime as an ISO string with trailing Z."""
    if dt.tzinfo is not None:
        # normalize to UTC first if somehow aware
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    # ensure seconds precision; keep microseconds if present
    s = dt.isoformat()
    # append Z to indicate UTC
    return s + "Z"
