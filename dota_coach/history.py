"""SQLite match history persistence.

Stores MatchReport JSON per (match_id, account_id, role) in
~/.dota_coach/history.db. This is the v3 foundation for cross-session chat,
Turbo benchmark accumulation, and 'analyze previous match' features.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

DB_PATH = Path.home() / ".dota_coach" / "history.db"

logger = logging.getLogger(__name__)

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS match_history (
    match_id    INTEGER NOT NULL,
    account_id  INTEGER NOT NULL,
    role        INTEGER NOT NULL,
    report_json TEXT    NOT NULL,
    analyzed_at TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (match_id, account_id, role)
);
CREATE INDEX IF NOT EXISTS idx_account_analyzed
    ON match_history (account_id, analyzed_at DESC);
"""


def _ensure_db() -> None:
    """Create the DB file and schema if they don't exist."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript(_CREATE_SQL)
        conn.commit()


@contextmanager
def _db() -> Iterator[sqlite3.Connection]:
    """Context manager that opens, yields, and closes a DB connection."""
    _ensure_db()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def save_match_report(
    match_id: int, account_id: int, role: int, report: dict, analyzed_at: str | None = None
) -> None:
    """Upsert a MatchReport dict to match_history.

    Silently logs and swallows errors so a DB failure never breaks the API.

    Args:
        match_id: The match ID
        account_id: The account ID
        role: The role number
        report: The report dict to save
        analyzed_at: Optional explicit timestamp (ISO format). If None, uses current time.
    """
    try:
        serialised = json.dumps(report)
    except (TypeError, ValueError) as exc:
        logger.warning("Cannot serialise report for history DB: %s", exc)
        return
    try:
        with _db() as conn:
            if analyzed_at is not None:
                conn.execute(
                    """
                    INSERT INTO match_history (match_id, account_id, role, report_json, analyzed_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT (match_id, account_id, role)
                    DO UPDATE SET report_json = excluded.report_json,
                                  analyzed_at = excluded.analyzed_at
                    """,
                    (match_id, account_id, role, serialised, analyzed_at),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO match_history (match_id, account_id, role, report_json)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT (match_id, account_id, role)
                    DO UPDATE SET report_json = excluded.report_json,
                                  analyzed_at = datetime('now')
                    """,
                    (match_id, account_id, role, serialised),
                )
            conn.commit()
    except Exception as exc:
        logger.warning("Failed to save match %s to history DB: %s", match_id, exc)


def get_match_history(account_id: int, limit: int = 20) -> list[dict]:
    """Return the most recent MatchReport dicts for an account, newest first.

    Returns an empty list on any DB error.
    """
    try:
        with _db() as conn:
            rows = conn.execute(
                """
                SELECT report_json FROM match_history
                WHERE account_id = ?
                ORDER BY analyzed_at DESC
                LIMIT ?
                """,
                (account_id, limit),
            ).fetchall()
        return [json.loads(r["report_json"]) for r in rows]
    except Exception as exc:
        logger.warning("Failed to load history for account %s: %s", account_id, exc)
        return []


def get_stored_report(match_id: int, account_id: int, role: int) -> dict | None:
    """Return a stored MatchReport for the given (match_id, account_id, role), or None."""
    try:
        with _db() as conn:
            row = conn.execute(
                "SELECT report_json FROM match_history WHERE match_id=? AND account_id=? AND role=?",
                (match_id, account_id, role),
            ).fetchone()
        return json.loads(row["report_json"]) if row else None
    except Exception as exc:
        logger.warning("Failed to load stored report for match %s: %s", match_id, exc)
        return None


def count_hero_matches(account_id: int, hero: str, turbo: bool | None = None) -> int:
    """Return the number of stored matches for a specific hero.

    Args:
        account_id: The account ID to filter by.
        hero: Hero name (must match json_extract('$.hero')).
        turbo: If True/False, filter to turbo/non-turbo matches. None counts all.
    """
    try:
        with _db() as conn:
            if turbo is None:
                row = conn.execute(
                    """
                    SELECT COUNT(*) as cnt FROM match_history
                    WHERE account_id = ?
                      AND json_extract(report_json, '$.hero') = ?
                    """,
                    (account_id, hero),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT COUNT(*) as cnt FROM match_history
                    WHERE account_id = ?
                      AND json_extract(report_json, '$.hero') = ?
                      AND json_extract(report_json, '$.turbo') = ?
                    """,  # json_extract returns NULL for missing keys; MatchReport always serializes turbo via Pydantic
                    (account_id, hero, 1 if turbo else 0),
                ).fetchone()
        return int(row["cnt"]) if row else 0
    except Exception as exc:
        logger.warning("Failed to count hero matches for %s: %s", hero, exc)
        return 0


def get_analyzed_ids(account_id: int) -> set[int]:
    """Return a set of match IDs that have been analyzed for an account.

    Returns an empty set on any DB error.
    """
    try:
        with _db() as conn:
            rows = conn.execute(
                "SELECT match_id FROM match_history WHERE account_id = ?",
                (account_id,),
            ).fetchall()
        return {int(r["match_id"]) for r in rows}
    except Exception as exc:
        logger.warning("Failed to load analyzed IDs for account %s: %s", account_id, exc)
        return set()


def _percentile_from_sorted(values: list[float], pct: float) -> float:
    """Interpolate a percentile from a sorted list of values (0.0-1.0 scale)."""
    if not values:
        return 0.0
    n = len(values)
    idx = pct * (n - 1)
    lo, hi = int(idx), min(int(idx) + 1, n - 1)
    frac = idx - lo
    return values[lo] + frac * (values[hi] - values[lo])


def _player_pct_from_sorted(values: list[float], player_value: float) -> float:
    """Return the fraction of values strictly below player_value."""
    if not values:
        return 0.0
    below = sum(1 for v in values if v < player_value)
    return below / len(values)


_LOCAL_THRESHOLD = 30
_LOCAL_MAX_SAMPLE = 500  # Max number of past matches to include in local percentile computation
_METRIC_QUERY: dict[str, tuple[str, str | None]] = {
    # metric_name -> (primary_json_path, secondary_json_path_or_None)
    # secondary is used only for derived metrics (last_hits_per_min)
    "gold_per_min":      ("$.metrics.gpm",              None),
    "xp_per_min":        ("$.metrics.xpm",              None),
    "last_hits_per_min": ("$.metrics.total_last_hits",  "$.metrics.duration_minutes"),
}


def get_local_benchmarks(
    account_id: int, hero: str, metrics: list[str]
) -> tuple[list, int]:
    """Return (list[LocalBenchmark], sample_size) for non-turbo matches on this hero.

    Returns ([], count) when count < _LOCAL_THRESHOLD.
    The player_value used for percentile ranking is the most recent match's value.
    LocalBenchmark is imported lazily to avoid circular imports.
    """
    from dota_coach.models import LocalBenchmark

    try:
        with _db() as conn:
            # Count non-turbo matches for this hero
            count_row = conn.execute(
                """
                SELECT COUNT(*) as cnt FROM match_history
                WHERE account_id = ?
                  AND json_extract(report_json, '$.hero') = ?
                  AND json_extract(report_json, '$.turbo') = 0  -- json_extract returns NULL for missing keys; MatchReport always serializes turbo via Pydantic
                """,
                (account_id, hero),
            ).fetchone()
            count = int(count_row["cnt"]) if count_row else 0

            if count < _LOCAL_THRESHOLD:
                return [], count

            result: list[LocalBenchmark] = []
            for metric in metrics:
                if metric not in _METRIC_QUERY:
                    continue
                primary_path, secondary_path = _METRIC_QUERY[metric]

                # primary_path and secondary_path are internal constants from _METRIC_QUERY,
                # never user-controlled — safe to interpolate into SQL.
                if secondary_path is None:
                    # Direct field
                    rows = conn.execute(
                        f"""
                        SELECT json_extract(report_json, '{primary_path}') as val
                        FROM match_history
                        WHERE account_id = ?
                          AND json_extract(report_json, '$.hero') = ?
                          AND json_extract(report_json, '$.turbo') = 0
                          AND json_extract(report_json, '{primary_path}') IS NOT NULL
                        ORDER BY analyzed_at DESC, match_id DESC LIMIT {_LOCAL_MAX_SAMPLE}
                        """,
                        (account_id, hero),
                    ).fetchall()
                    values = [float(r["val"]) for r in rows if r["val"] is not None]
                else:
                    # Derived: primary / secondary
                    rows = conn.execute(
                        f"""
                        SELECT json_extract(report_json, '{primary_path}') as primary_val,
                               json_extract(report_json, '{secondary_path}') as secondary_val
                        FROM match_history
                        WHERE account_id = ?
                          AND json_extract(report_json, '$.hero') = ?
                          AND json_extract(report_json, '$.turbo') = 0
                          AND json_extract(report_json, '{primary_path}') IS NOT NULL
                          AND json_extract(report_json, '{secondary_path}') > 0
                        ORDER BY analyzed_at DESC, match_id DESC LIMIT {_LOCAL_MAX_SAMPLE}
                        """,
                        (account_id, hero),
                    ).fetchall()
                    values = [
                        float(r["primary_val"]) / float(r["secondary_val"])
                        for r in rows
                        if r["primary_val"] is not None and r["secondary_val"]
                    ]

                if not values:
                    continue

                sorted_vals = sorted(values)
                # player_value = most recent match (first row in DESC order)
                player_value = values[0]
                # player_pct uses "fraction strictly below" convention (not interpolation) —
                # matches differ from p25/median/p75 by design; intuitive for "beats X% of your games"
                player_pct = _player_pct_from_sorted(sorted_vals, player_value)

                result.append(LocalBenchmark(
                    metric=metric,
                    player_value=player_value,
                    player_pct=player_pct,
                    p25=_percentile_from_sorted(sorted_vals, 0.25),
                    median=_percentile_from_sorted(sorted_vals, 0.50),
                    p75=_percentile_from_sorted(sorted_vals, 0.75),
                    sample_size=len(sorted_vals),
                ))

            return result, count

    except Exception as exc:
        logger.warning("Failed to compute local benchmarks for %s: %s", hero, exc)
        return [], 0
