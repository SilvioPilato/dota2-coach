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


def save_match_report(match_id: int, account_id: int, role: int, report: dict) -> None:
    """Upsert a MatchReport dict to match_history.

    Silently logs and swallows errors so a DB failure never breaks the API.
    """
    try:
        serialised = json.dumps(report)
    except (TypeError, ValueError) as exc:
        logger.warning("Cannot serialise report for history DB: %s", exc)
        return
    try:
        with _db() as conn:
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


def count_hero_matches(account_id: int, hero: str) -> int:
    """Return the number of stored matches for a specific hero (for Turbo benchmark progress)."""
    try:
        with _db() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) as cnt FROM match_history
                WHERE account_id = ?
                  AND json_extract(report_json, '$.hero') = ?
                """,
                (account_id, hero),
            ).fetchone()
        return int(row["cnt"]) if row else 0
    except Exception as exc:
        logger.warning("Failed to count hero matches for %s: %s", hero, exc)
        return 0
