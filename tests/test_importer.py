"""Tests for dota_coach.importer — metrics-only match import pipeline."""
from __future__ import annotations

import gc
import json
import tempfile
from pathlib import Path
from unittest import mock
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from dota_coach.history import _ensure_db, _db


@pytest.fixture
def temp_db():
    with tempfile.TemporaryDirectory() as tmpdir:
        temp_path = Path(tmpdir) / "test_import.db"
        with mock.patch("dota_coach.history.DB_PATH", temp_path):
            _ensure_db()
            yield temp_path
            gc.collect()


def _fake_match_list():
    return [{"match_id": 1001}, {"match_id": 1002}]


def _fake_match_meta(match_id: int, account_id: int) -> dict:
    return {
        "match_id": match_id,
        "game_mode": 1,
        "patch": "7.37",
        "players": [{"account_id": account_id, "hero_id": 1, "lane_role": 1,
                      "player_slot": 0, "rank_tier": 50}],
    }


def _fake_metrics(match_id: int):
    from dota_coach.models import MatchMetrics
    return MatchMetrics(
        match_id=match_id, hero="Anti-Mage", duration_minutes=35.0, result="win",
        lh_at_10=60, denies_at_10=5, deaths_before_10=0,
        death_timestamps_laning=[], net_worth_at_10=8000, net_worth_at_20=16000,
        opponent_net_worth_at_10=7500, opponent_net_worth_at_20=15000,
        gpm=480, xpm=600, total_last_hits=200,
        first_core_item_minute=None, first_core_item_name=None,
        laning_heatmap_own_half_pct=0.4, ward_purchases=0,
        teamfight_participation_rate=0.6, teamfight_avg_damage_contribution=None,
        first_roshan_minute=None, first_tower_minute=None, turbo=False,
    )


def _fake_enrichment():
    from dota_coach.models import EnrichmentContext
    return EnrichmentContext(patch_name="7.37", benchmarks=[], item_costs={}, hero_base_stats={})


class TestImportMatchMetrics:
    async def test_imports_new_matches_and_saves_metrics_only(self, temp_db):
        """New matches are fetched, extracted, and saved with metrics_only=True."""
        account_id = 123

        with patch("dota_coach.history.DB_PATH", temp_db), \
             patch("dota_coach.importer.get_paginated_matches",
                   new_callable=AsyncMock, return_value=_fake_match_list()), \
             patch("dota_coach.importer.get_match",
                   new_callable=AsyncMock, side_effect=lambda mid: _fake_match_meta(mid, account_id)), \
             patch("dota_coach.importer.get_match_positions",
                   new_callable=AsyncMock, return_value={account_id: 1}), \
             patch("dota_coach.importer.extract_metrics_from_opendota",
                   return_value=_fake_metrics(1001)), \
             patch("dota_coach.importer.enrich",
                   new_callable=AsyncMock, return_value=_fake_enrichment()):
            from dota_coach.importer import import_match_metrics
            result = await import_match_metrics(account_id, limit=10)

        assert result["imported"] == 2
        assert result["skipped"] == 0
        assert result["failed"] == 0

        # Verify reports saved with metrics_only=True
        with mock.patch("dota_coach.history.DB_PATH", temp_db):
            with _db() as conn:
                rows = conn.execute("SELECT report_json FROM match_history WHERE account_id=?",
                                    (account_id,)).fetchall()
        assert len(rows) == 2
        for row in rows:
            report = json.loads(row["report_json"])
            assert report["metrics_only"] is True
            assert report["coaching_report"] == ""

    async def test_skips_already_stored_matches(self, temp_db):
        """Matches already in the DB are skipped."""
        account_id = 123

        # Pre-store match 1001
        from dota_coach.history import save_match_report
        with patch("dota_coach.history.DB_PATH", temp_db):
            save_match_report(1001, account_id, 1, {"hero": "Anti-Mage", "turbo": False,
                                                     "metrics": {}})

        with patch("dota_coach.history.DB_PATH", temp_db), \
             patch("dota_coach.importer.get_paginated_matches",
                   new_callable=AsyncMock, return_value=_fake_match_list()), \
             patch("dota_coach.importer.get_match",
                   new_callable=AsyncMock, side_effect=lambda mid: _fake_match_meta(mid, account_id)), \
             patch("dota_coach.importer.get_match_positions",
                   new_callable=AsyncMock, return_value={account_id: 1}), \
             patch("dota_coach.importer.extract_metrics_from_opendota",
                   return_value=_fake_metrics(1002)), \
             patch("dota_coach.importer.enrich",
                   new_callable=AsyncMock, return_value=_fake_enrichment()):
            from dota_coach.importer import import_match_metrics
            result = await import_match_metrics(account_id, limit=10)

        assert result["imported"] == 1
        assert result["skipped"] == 1

    async def test_failed_match_does_not_abort_pipeline(self, temp_db):
        """A match that fails extraction is counted as failed, pipeline continues."""
        account_id = 123

        def raise_on_1001(mid):
            if mid == 1001:
                raise ValueError("extraction failed")
            return _fake_match_meta(mid, account_id)

        with patch("dota_coach.history.DB_PATH", temp_db), \
             patch("dota_coach.importer.get_paginated_matches",
                   new_callable=AsyncMock, return_value=_fake_match_list()), \
             patch("dota_coach.importer.get_match",
                   new_callable=AsyncMock, side_effect=raise_on_1001), \
             patch("dota_coach.importer.get_match_positions",
                   new_callable=AsyncMock, return_value={account_id: 1}), \
             patch("dota_coach.importer.extract_metrics_from_opendota",
                   return_value=_fake_metrics(1002)), \
             patch("dota_coach.importer.enrich",
                   new_callable=AsyncMock, return_value=_fake_enrichment()):
            from dota_coach.importer import import_match_metrics
            result = await import_match_metrics(account_id, limit=10)

        assert result["failed"] == 1
        assert result["imported"] == 1

    async def test_undetermined_role_counts_as_failed(self, temp_db):
        """If role cannot be determined, match is skipped and counted as failed."""
        account_id = 123

        with patch("dota_coach.history.DB_PATH", temp_db), \
             patch("dota_coach.importer.get_paginated_matches",
                   new_callable=AsyncMock, return_value=[{"match_id": 1001}]), \
             patch("dota_coach.importer.get_match",
                   new_callable=AsyncMock, return_value=_fake_match_meta(1001, account_id)), \
             patch("dota_coach.importer.get_match_positions",
                   new_callable=AsyncMock, return_value={}), \
             patch("dota_coach.importer.detect_role",
                   side_effect=ValueError("cannot determine role")):
            from dota_coach.importer import import_match_metrics
            result = await import_match_metrics(account_id, limit=10)

        assert result["failed"] == 1
        assert result["imported"] == 0
