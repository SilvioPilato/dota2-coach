"""Disk cache helpers for analysis results and decompressed replay files.

Note: decompressed .dem files are typically 50–200 MB each.
The cache directory (~/.dota_coach/cache/) can be manually cleared to reclaim disk space.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

CACHE_DIR = Path.home() / ".dota_coach" / "cache"

ANALYSIS_TTL = 3600 * 24 * 7  # 7 days
DEM_TTL      = 3600 * 24 * 7  # 7 days

logger = logging.getLogger(__name__)


def _analysis_path(match_id: int, account_id: int, role: int) -> Path:
    return CACHE_DIR / f"analysis_{match_id}_{account_id}_{role}.json"


def read_analysis_cache(match_id: int, account_id: int, role: int) -> dict | None:
    """Return cached analysis JSON or None (missing, expired, or corrupt)."""
    path = _analysis_path(match_id, account_id, role)
    if not path.exists():
        return None
    try:
        if time.time() - path.stat().st_mtime > ANALYSIS_TTL:
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logger.warning("Corrupt analysis cache at %s — ignoring", path)
        return None
    except Exception:
        return None


def write_analysis_cache(match_id: int, account_id: int, role: int, data: dict) -> None:
    """Write analysis result to disk atomically. Logs a warning if serialisation fails."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _analysis_path(match_id, account_id, role)
    tmp = path.with_suffix(".tmp")
    try:
        serialised = json.dumps(data)
    except (TypeError, ValueError) as exc:
        logger.warning("Cannot serialise analysis result for cache: %s", exc)
        return
    tmp.write_text(serialised, encoding="utf-8")
    tmp.rename(path)


def get_dem_cache_path(match_id: int) -> Path:
    """Return the Path where a decompressed .dem should be stored, creating CACHE_DIR if needed."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"replay_{match_id}.dem"


def is_dem_cache_fresh(match_id: int) -> bool:
    """Return True if the cached .dem exists and is within TTL."""
    path = CACHE_DIR / f"replay_{match_id}.dem"
    if not path.exists():
        return False
    try:
        return time.time() - path.stat().st_mtime <= DEM_TTL
    except Exception:
        return False


def invalidate_dem_cache(match_id: int) -> None:
    """Delete the cached .dem file if present; no-op if absent."""
    path = CACHE_DIR / f"replay_{match_id}.dem"
    try:
        path.unlink(missing_ok=True)
    except Exception:
        pass
