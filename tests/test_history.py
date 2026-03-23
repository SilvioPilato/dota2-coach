"""Tests for dota_coach.history — SQLite match history persistence."""
from __future__ import annotations

import gc
import json
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from dota_coach.history import (
    get_analyzed_ids,
    get_match_history,
    get_stored_report,
    save_match_report,
    count_hero_matches,
    _db,
    _ensure_db,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_db():
    """Replace DB_PATH with a temporary database for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        temp_path = Path(tmpdir) / "test_history.db"
        with mock.patch("dota_coach.history.DB_PATH", temp_path):
            # Ensure DB is initialized
            _ensure_db()
            yield temp_path
            # Force garbage collection to close DB connections
            gc.collect()


def _make_report(hero: str = "Juggernaut", gpm: int = 400) -> dict:
    """Create a minimal MatchReport dict."""
    return {
        "hero": hero,
        "gpm": gpm,
        "match_id": 999,
        "account_id": 123,
        "role": 1,
    }


# ---------------------------------------------------------------------------
# save_match_report and retrieval tests
# ---------------------------------------------------------------------------


class TestSaveMatchReport:
    def test_save_and_retrieve_basic_report(self, temp_db):
        """Test saving a report and retrieving it."""
        report = _make_report()
        save_match_report(999, 123, 1, report)

        # Verify via DB query
        with _db() as conn:
            row = conn.execute(
                "SELECT report_json FROM match_history WHERE match_id=? AND account_id=? AND role=?",
                (999, 123, 1),
            ).fetchone()
        assert row is not None
        retrieved = json.loads(row["report_json"])
        assert retrieved["hero"] == "Juggernaut"

    def test_save_multiple_reports_different_roles(self, temp_db):
        """Test saving the same match with different roles."""
        report1 = _make_report(hero="Juggernaut")
        report2 = _make_report(hero="Anti-Mage")

        save_match_report(999, 123, 1, report1)
        save_match_report(999, 123, 2, report2)

        with _db() as conn:
            rows = conn.execute(
                "SELECT role, report_json FROM match_history WHERE match_id=? AND account_id=?",
                (999, 123),
            ).fetchall()
        assert len(rows) == 2
        assert rows[0]["role"] in [1, 2]

    def test_save_with_invalid_json_logs_warning(self, temp_db, caplog):
        """Test that non-serializable objects are logged and skipped."""
        report = {"circular": {}}
        report["circular"]["self"] = report  # Create circular reference

        save_match_report(999, 123, 1, report)

        # Should log a warning
        assert "Cannot serialise report" in caplog.text
        # Should not have been saved
        with _db() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM match_history WHERE match_id=?",
                (999,),
            ).fetchone()
        assert row["cnt"] == 0


# ---------------------------------------------------------------------------
# get_analyzed_ids tests
# ---------------------------------------------------------------------------


class TestGetAnalyzedIds:
    def test_empty_for_nonexistent_account(self, temp_db):
        """Test that a non-existent account returns empty set."""
        result = get_analyzed_ids(999)
        assert result == set()

    def test_single_match_id(self, temp_db):
        """Test retrieving a single match ID."""
        report = _make_report()
        save_match_report(100, 123, 1, report)

        result = get_analyzed_ids(123)
        assert result == {100}

    def test_multiple_match_ids_same_account(self, temp_db):
        """Test retrieving multiple match IDs for the same account."""
        report1 = _make_report()
        report2 = _make_report()
        report3 = _make_report()

        save_match_report(100, 123, 1, report1)
        save_match_report(101, 123, 2, report2)
        save_match_report(102, 123, 1, report3)

        result = get_analyzed_ids(123)
        assert result == {100, 101, 102}

    def test_duplicate_match_ids_different_roles(self, temp_db):
        """Test that duplicate match IDs with different roles return unique set."""
        report = _make_report()
        save_match_report(100, 123, 1, report)
        save_match_report(100, 123, 2, report)

        result = get_analyzed_ids(123)
        # Should be {100}, not {100, 100}
        assert result == {100}

    def test_account_isolation(self, temp_db):
        """Test that IDs from different accounts don't mix."""
        report = _make_report()
        save_match_report(100, 123, 1, report)
        save_match_report(200, 456, 1, report)

        result_123 = get_analyzed_ids(123)
        result_456 = get_analyzed_ids(456)

        assert result_123 == {100}
        assert result_456 == {200}

    def test_returns_integers(self, temp_db):
        """Test that match IDs are returned as integers."""
        report = _make_report()
        save_match_report(123456789, 123, 1, report)

        result = get_analyzed_ids(123)
        assert len(result) == 1
        match_id = next(iter(result))
        assert isinstance(match_id, int)
        assert match_id == 123456789

    def test_returns_empty_set_on_db_error(self, temp_db, caplog):
        """Test that DB errors are logged and empty set is returned."""
        # Mock the _db context manager to raise an exception
        with mock.patch("dota_coach.history._db", side_effect=Exception("DB error")):
            result = get_analyzed_ids(123)

        assert result == set()
        assert "Failed to load analyzed IDs" in caplog.text


# ---------------------------------------------------------------------------
# get_match_history tests
# ---------------------------------------------------------------------------


class TestGetMatchHistory:
    def test_empty_for_nonexistent_account(self, temp_db):
        """Test empty list for non-existent account."""
        result = get_match_history(999)
        assert result == []

    def test_single_match(self, temp_db):
        """Test retrieving a single match report."""
        report = _make_report(hero="Juggernaut")
        save_match_report(100, 123, 1, report)

        result = get_match_history(123, limit=20)
        assert len(result) == 1
        assert result[0]["hero"] == "Juggernaut"

    def test_limit_respected(self, temp_db):
        """Test that limit parameter is respected."""
        for i in range(10):
            report = _make_report(hero=f"Hero{i}")
            save_match_report(100 + i, 123, 1, report)

        result = get_match_history(123, limit=3)
        assert len(result) == 3

    def test_newest_first(self, temp_db):
        """Test that results are ordered by analyzed_at DESC."""
        report1 = _make_report(hero="Hero1")
        report2 = _make_report(hero="Hero2")

        # Use explicit timestamps to control ordering without sleeping
        save_match_report(100, 123, 1, report1, analyzed_at="2024-01-01 10:00:00")
        save_match_report(101, 123, 1, report2, analyzed_at="2024-01-01 10:00:01")

        result = get_match_history(123, limit=20)
        # The second saved (newer timestamp) should be first
        assert result[0]["hero"] == "Hero2"
        assert result[1]["hero"] == "Hero1"


# ---------------------------------------------------------------------------
# get_stored_report tests
# ---------------------------------------------------------------------------


class TestGetStoredReport:
    def test_retrieve_exact_match(self, temp_db):
        """Test retrieving a report by exact (match_id, account_id, role)."""
        report = _make_report(hero="Anti-Mage")
        save_match_report(100, 123, 1, report)

        result = get_stored_report(100, 123, 1)
        assert result is not None
        assert result["hero"] == "Anti-Mage"

    def test_return_none_if_not_found(self, temp_db):
        """Test that None is returned if report doesn't exist."""
        result = get_stored_report(999, 999, 1)
        assert result is None

    def test_role_specific_retrieval(self, temp_db):
        """Test that different roles are stored/retrieved separately."""
        report1 = _make_report(hero="Juggernaut")
        report2 = _make_report(hero="Anti-Mage")

        save_match_report(100, 123, 1, report1)
        save_match_report(100, 123, 2, report2)

        result1 = get_stored_report(100, 123, 1)
        result2 = get_stored_report(100, 123, 2)

        assert result1["hero"] == "Juggernaut"
        assert result2["hero"] == "Anti-Mage"


# ---------------------------------------------------------------------------
# count_hero_matches tests
# ---------------------------------------------------------------------------


class TestCountHeroMatches:
    def test_zero_for_nonexistent_account(self, temp_db):
        """Test zero count for non-existent account."""
        result = count_hero_matches(999, "Juggernaut")
        assert result == 0

    def test_single_match_count(self, temp_db):
        """Test counting a single match."""
        report = _make_report(hero="Juggernaut")
        save_match_report(100, 123, 1, report)

        result = count_hero_matches(123, "Juggernaut")
        assert result == 1

    def test_multiple_matches_same_hero(self, temp_db):
        """Test counting multiple matches with the same hero."""
        for i in range(5):
            report = _make_report(hero="Juggernaut")
            save_match_report(100 + i, 123, 1, report)

        result = count_hero_matches(123, "Juggernaut")
        assert result == 5

    def test_hero_isolation(self, temp_db):
        """Test that different heroes have separate counts."""
        report1 = _make_report(hero="Juggernaut")
        report2 = _make_report(hero="Anti-Mage")

        save_match_report(100, 123, 1, report1)
        save_match_report(101, 123, 1, report2)

        result_jug = count_hero_matches(123, "Juggernaut")
        result_am = count_hero_matches(123, "Anti-Mage")

        assert result_jug == 1
        assert result_am == 1

    def test_zero_for_nonexistent_hero(self, temp_db):
        """Test zero count for hero that was never played."""
        report = _make_report(hero="Juggernaut")
        save_match_report(100, 123, 1, report)

        result = count_hero_matches(123, "Tidehunter")
        assert result == 0
