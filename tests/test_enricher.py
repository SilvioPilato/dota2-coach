"""Tests for dota_coach.enricher — cache hit/miss + HTTP mocking."""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from dota_coach.enricher import (
    CACHE_DIR,
    _interpolate_pct,
    _median_from_benchmarks,
    _read_cache,
    _write_cache,
    enrich,
)
from dota_coach.models import MatchMetrics


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_metrics(**overrides) -> MatchMetrics:
    defaults = dict(
        match_id=1,
        hero="DrowRanger",
        duration_minutes=35.0,
        result="win",
        lh_at_10=50, denies_at_10=5,
        deaths_before_10=0, death_timestamps_laning=[],
        net_worth_at_10=3500, enemy_carry_net_worth_at_10=3200,
        net_worth_at_20=8000, enemy_carry_net_worth_at_20=7000,
        gpm=400, xpm=500,
        first_core_item_minute=15.0, first_core_item_name="item_battle_fury",
        laning_heatmap_own_half_pct=0.60, ward_purchases=0,
        teamfight_participation_rate=0.55,
        teamfight_avg_damage_contribution=0.20,
        first_roshan_minute=None, first_tower_minute=None,
    )
    defaults.update(overrides)
    return MatchMetrics(**defaults)


FAKE_HEROES = {
    "6": {
        "localized_name": "Drow Ranger",
        "name": "npc_dota_hero_drow_ranger",
        "base_attack_min": 46,
        "base_attack_max": 51,
        "move_speed": 285,
        "attack_range": 625,
        "base_armor": -1,
    }
}

FAKE_BENCHMARKS = {
    "hero_id": 6,
    "result": {
        "gold_per_min": [
            {"percentile": 0.1, "value": 250},
            {"percentile": 0.2, "value": 300},
            {"percentile": 0.3, "value": 350},
            {"percentile": 0.5, "value": 420},
            {"percentile": 0.7, "value": 500},
            {"percentile": 0.9, "value": 600},
            {"percentile": 0.99, "value": 750},
        ],
        "xp_per_min": [
            {"percentile": 0.1, "value": 300},
            {"percentile": 0.5, "value": 500},
            {"percentile": 0.9, "value": 700},
        ],
    },
}

FAKE_ITEMS = {
    "item_battle_fury": {"cost": 4600},
    "item_manta": {"cost": 4900},
}

FAKE_MATCH_META = {"patch": "7.38c", "players": []}


# ---------------------------------------------------------------------------
# _interpolate_pct
# ---------------------------------------------------------------------------

class TestInterpolatePct:
    def test_exact_match(self):
        pts = [{"percentile": 0.5, "value": 400}]
        assert _interpolate_pct(pts, 400) == 0.5

    def test_below_minimum(self):
        pts = [{"percentile": 0.1, "value": 200}, {"percentile": 0.9, "value": 600}]
        assert _interpolate_pct(pts, 100) == 0.1

    def test_above_maximum(self):
        pts = [{"percentile": 0.1, "value": 200}, {"percentile": 0.9, "value": 600}]
        assert _interpolate_pct(pts, 700) == 0.9

    def test_interpolation_midpoint(self):
        pts = [{"percentile": 0.2, "value": 300}, {"percentile": 0.8, "value": 600}]
        pct = _interpolate_pct(pts, 450)  # halfway between 300 and 600
        assert 0.45 < pct < 0.55

    def test_empty_returns_median(self):
        assert _interpolate_pct([], 100) == 0.5


class TestMedianFromBenchmarks:
    def test_returns_closest_to_50pct(self):
        pts = [
            {"percentile": 0.1, "value": 200},
            {"percentile": 0.5, "value": 400},
            {"percentile": 0.9, "value": 600},
        ]
        assert _median_from_benchmarks(pts) == 400

    def test_empty_returns_zero(self):
        assert _median_from_benchmarks([]) == 0.0


# ---------------------------------------------------------------------------
# Cache read/write
# ---------------------------------------------------------------------------

class TestCache:
    def test_write_and_read(self, tmp_path, monkeypatch):
        monkeypatch.setattr("dota_coach.enricher.CACHE_DIR", tmp_path)
        _write_cache("test.json", {"a": 1})
        result = _read_cache("test.json", ttl=3600)
        assert result == {"a": 1}

    def test_expired_cache_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr("dota_coach.enricher.CACHE_DIR", tmp_path)
        _write_cache("old.json", {"a": 1})
        # Backdate the file
        old_path = tmp_path / "old.json"
        old_time = time.time() - 99999
        import os
        os.utime(old_path, (old_time, old_time))
        result = _read_cache("old.json", ttl=3600)
        assert result is None

    def test_missing_file_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr("dota_coach.enricher.CACHE_DIR", tmp_path)
        assert _read_cache("nonexistent.json", ttl=3600) is None


# ---------------------------------------------------------------------------
# enrich (mocked HTTP)
# ---------------------------------------------------------------------------

class TestEnrich:
    def test_cold_cache_makes_http_calls(self, tmp_path, monkeypatch):
        monkeypatch.setattr("dota_coach.enricher.CACHE_DIR", tmp_path)

        async def mock_fetch(url):
            if "itemTimings" in url:
                return []
            if "heroes" in url:
                return FAKE_HEROES
            if "items" in url:
                return FAKE_ITEMS
            return {}

        with patch("dota_coach.enricher._fetch_json", side_effect=mock_fetch):
            with patch("dota_coach.enricher.get_benchmarks", new_callable=AsyncMock, return_value=FAKE_BENCHMARKS):
                ctx = asyncio.run(enrich(_make_metrics(), FAKE_MATCH_META, purchased_items=["item_battle_fury"]))

        assert ctx.patch_name == "7.38c"
        assert len(ctx.benchmarks) > 0
        gpm_bench = next(b for b in ctx.benchmarks if b.metric == "gold_per_min")
        assert gpm_bench.player_pct > 0

    def test_warm_cache_no_http(self, tmp_path, monkeypatch):
        monkeypatch.setattr("dota_coach.enricher.CACHE_DIR", tmp_path)

        # Pre-populate cache
        _write_cache("heroes.json", FAKE_HEROES)
        _write_cache("items.json", FAKE_ITEMS)
        _write_cache("benchmarks_6.json", FAKE_BENCHMARKS)
        _write_cache("item_timings_6.json", [])

        mock_fetch = AsyncMock(side_effect=AssertionError("should not be called"))
        mock_bench = AsyncMock(side_effect=AssertionError("should not be called"))

        with patch("dota_coach.enricher._fetch_json", mock_fetch):
            with patch("dota_coach.enricher.get_benchmarks", mock_bench):
                ctx = asyncio.run(enrich(_make_metrics(), FAKE_MATCH_META))

        assert ctx.patch_name == "7.38c"
        assert len(ctx.benchmarks) > 0

    def test_hero_not_found_returns_empty_benchmarks(self, tmp_path, monkeypatch):
        monkeypatch.setattr("dota_coach.enricher.CACHE_DIR", tmp_path)

        async def mock_fetch(url):
            if "heroes" in url:
                return {}  # no heroes → hero_id = None
            if "items" in url:
                return FAKE_ITEMS
            return {}

        with patch("dota_coach.enricher._fetch_json", side_effect=mock_fetch):
            ctx = asyncio.run(enrich(_make_metrics(hero="UnknownHero"), FAKE_MATCH_META))

        assert ctx.benchmarks == []

    def test_item_costs_populated(self, tmp_path, monkeypatch):
        monkeypatch.setattr("dota_coach.enricher.CACHE_DIR", tmp_path)

        async def mock_fetch(url):
            if "itemTimings" in url:
                return []
            if "heroes" in url:
                return FAKE_HEROES
            if "items" in url:
                return FAKE_ITEMS
            return {}

        with patch("dota_coach.enricher._fetch_json", side_effect=mock_fetch):
            with patch("dota_coach.enricher.get_benchmarks", new_callable=AsyncMock, return_value=FAKE_BENCHMARKS):
                ctx = asyncio.run(enrich(
                    _make_metrics(),
                    FAKE_MATCH_META,
                    purchased_items=["item_battle_fury"],
                ))

        assert ctx.item_costs.get("item_battle_fury") == 4600
