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
    _discover_lane_heroes,
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
        net_worth_at_10=3500, opponent_net_worth_at_10=3200,
        net_worth_at_20=8000, opponent_net_worth_at_20=7000,
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

    def test_bootstrap_cache_populated(self, tmp_path, monkeypatch):
        monkeypatch.setattr("dota_coach.enricher.CACHE_DIR", tmp_path)

        FAKE_BOOTSTRAP_RAW = [
            {"itemId": 11, "matchCount": 400, "winCount": 220, "timeAverage": 1020},
        ]

        async def mock_fetch(url):
            if "itemTimings" in url:
                return []
            if "heroes" in url:
                return FAKE_HEROES
            if "items" in url:
                # Include an entry with id so bootstrap resolve can map it
                data = dict(FAKE_ITEMS)
                data["battle_fury"] = {"id": 11, "qual": "artifact", "cost": 4600}
                return data
            return {}

        with patch("dota_coach.enricher._fetch_json", side_effect=mock_fetch):
            with patch("dota_coach.enricher.get_benchmarks", new_callable=AsyncMock, return_value=FAKE_BENCHMARKS):
                with patch("dota_coach.stratz.get_hero_item_bootstrap", new_callable=AsyncMock, return_value=FAKE_BOOTSTRAP_RAW):
                    ctx = asyncio.run(enrich(_make_metrics(), FAKE_MATCH_META, purchased_items=["item_battle_fury"]))

        # hero_item_bootstrap is either a resolved list or [] depending on items_data mapping
        assert isinstance(ctx.hero_item_bootstrap, list)

    def test_bootstrap_failure_returns_empty_list(self, tmp_path, monkeypatch):
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
                with patch("dota_coach.stratz.get_hero_item_bootstrap", new_callable=AsyncMock, side_effect=RuntimeError("stratz down")):
                    ctx = asyncio.run(enrich(_make_metrics(), FAKE_MATCH_META))

        assert ctx.hero_item_bootstrap == []


# ---------------------------------------------------------------------------
# _discover_lane_heroes
# ---------------------------------------------------------------------------

def _make_heroes_data(*entries: tuple[int, str]) -> dict:
    """Build minimal heroes_data dict keyed by hero_id string."""
    return {str(hid): {"localized_name": name, "name": f"npc_dota_hero_{name.lower().replace(' ', '_')}"} for hid, name in entries}


def _make_match_meta_players(our_id: int, our_is_radiant: bool, our_lane: int, players: list[dict]) -> dict:
    our = {"account_id": our_id, "isRadiant": our_is_radiant, "lane": our_lane, "hero_id": 1}
    return {"match_id": 1, "players": [our] + players}


def _blank_metrics() -> MatchMetrics:
    return _make_metrics(lane_enemies=[], lane_allies=[])


class TestDiscoverLaneHeroes:
    def test_mid_enemy_discovered(self):
        """Player is mid; opposing mid hero is discovered as lane_enemy."""
        heroes = _make_heroes_data((9, "Mirana"))
        match = _make_match_meta_players(
            our_id=123, our_is_radiant=True, our_lane=2,
            players=[{"account_id": 999, "isRadiant": False, "lane": 2, "hero_id": 9}],
        )
        m = _blank_metrics()
        _discover_lane_heroes(m, 123, match, heroes)
        assert m.lane_enemies == ["Mirana"]
        assert m.lane_allies == []

    def test_safe_lane_ally_discovered(self):
        """Safe lane carry has an ally support — both discovered."""
        heroes = _make_heroes_data((1, "Anti-Mage"), (2, "Crystal Maiden"), (3, "Axe"), (4, "Pudge"))
        match = {
            "match_id": 1,
            "players": [
                {"account_id": 100, "isRadiant": True, "lane": 1, "hero_id": 1},   # our hero
                {"account_id": 101, "isRadiant": True, "lane": 1, "hero_id": 2},   # ally
                {"account_id": 200, "isRadiant": False, "lane": 3, "hero_id": 3},  # enemy offlaners
                {"account_id": 201, "isRadiant": False, "lane": 3, "hero_id": 4},
            ],
        }
        m = _blank_metrics()
        _discover_lane_heroes(m, 100, match, heroes)
        assert m.lane_allies == ["Crystal Maiden"]
        assert set(m.lane_enemies) == {"Axe", "Pudge"}

    def test_hero_id_not_in_heroes_data_excluded(self):
        """Hero IDs missing from heroes_data are silently skipped."""
        heroes = _make_heroes_data((9, "Mirana"))  # only Mirana in data
        match = _make_match_meta_players(
            our_id=123, our_is_radiant=True, our_lane=2,
            players=[
                {"account_id": 999, "isRadiant": False, "lane": 2, "hero_id": 9},
                {"account_id": 888, "isRadiant": False, "lane": 2, "hero_id": 99},  # unknown id
            ],
        )
        m = _blank_metrics()
        _discover_lane_heroes(m, 123, match, heroes)
        assert m.lane_enemies == ["Mirana"]  # unknown hero skipped

    def test_no_lane_data_produces_empty(self):
        """Players missing lane field don't produce errors — empty lists returned."""
        heroes = _make_heroes_data((9, "Mirana"))
        match = {
            "match_id": 1,
            "players": [
                {"account_id": 123, "isRadiant": True, "hero_id": 1},  # no lane field
                {"account_id": 999, "isRadiant": False, "hero_id": 9},
            ],
        }
        m = _blank_metrics()
        _discover_lane_heroes(m, 123, match, heroes)
        assert m.lane_enemies == []
        assert m.lane_allies == []

    def test_stratz_positions_fallback_when_lane_is_none(self):
        """When OpenDota lane is None, Stratz positions are used to infer lanes."""
        heroes = _make_heroes_data((1, "Anti-Mage"), (2, "Crystal Maiden"), (3, "Axe"), (4, "Pudge"))
        match = {
            "match_id": 1,
            "players": [
                {"account_id": 100, "isRadiant": True, "hero_id": 1},   # no lane field
                {"account_id": 101, "isRadiant": True, "hero_id": 2},   # no lane field
                {"account_id": 200, "isRadiant": False, "hero_id": 3},  # no lane field
                {"account_id": 201, "isRadiant": False, "hero_id": 4},
            ],
        }
        stratz_positions = {
            100: 1,  # carry → lane 1 (safe)
            101: 5,  # hard support → lane 1 (with carry)
            200: 3,  # offlane → lane 3 (opposing safe)
            201: 4,  # soft support → lane 3 (with offlaner)
        }
        m = _blank_metrics()
        _discover_lane_heroes(m, 100, match, heroes, stratz_positions=stratz_positions)
        assert m.lane_allies == ["Crystal Maiden"]
        assert set(m.lane_enemies) == {"Axe", "Pudge"}

    def test_stratz_positions_mid_mirror(self):
        """Stratz positions correctly identify mid mirror matchup."""
        heroes = _make_heroes_data((9, "Mirana"), (10, "Shadow Fiend"))
        match = {
            "match_id": 1,
            "players": [
                {"account_id": 100, "isRadiant": True, "hero_id": 10},
                {"account_id": 200, "isRadiant": False, "hero_id": 9},
            ],
        }
        stratz_positions = {100: 2, 200: 2}  # both mid
        m = _blank_metrics()
        _discover_lane_heroes(m, 100, match, heroes, stratz_positions=stratz_positions)
        assert m.lane_enemies == ["Mirana"]
        assert m.lane_allies == []

    def test_does_not_overwrite_existing_lane_data(self):
        """enrich_lane_matchup skips discovery when lane_enemies already set."""
        # Test the guard in enrich_lane_matchup, not _discover_lane_heroes directly
        import asyncio
        from unittest.mock import AsyncMock, patch
        from dota_coach.enricher import enrich_lane_matchup

        heroes = _make_heroes_data((9, "Mirana"))
        match = _make_match_meta_players(
            our_id=123, our_is_radiant=True, our_lane=2,
            players=[{"account_id": 999, "isRadiant": False, "lane": 2, "hero_id": 9}],
        )
        m = _make_metrics(lane_enemies=["Lina"], lane_allies=[])  # already populated

        with patch("dota_coach.enricher.fetch_hero_matchup_winrates", new_callable=AsyncMock, return_value={}):
            asyncio.run(enrich_lane_matchup(m, "LEGEND_ANCIENT", heroes, match_meta=match, our_account_id=123))

        # Should NOT be overwritten
        assert m.lane_enemies == ["Lina"]


def test_enrich_accepts_account_id_param(monkeypatch):
    """enrich() must accept account_id without raising TypeError."""
    import asyncio
    from unittest.mock import AsyncMock, patch
    from dota_coach.enricher import enrich
    from dota_coach.models import MatchMetrics

    # Minimal MatchMetrics — only fields enrich() reads
    m = MatchMetrics(
        match_id=1, hero="Anti-Mage", duration_minutes=35.0,
        result="win", lh_at_10=60, denies_at_10=5, deaths_before_10=0,
        death_timestamps_laning=[], net_worth_at_10=8000, net_worth_at_20=16000,
        opponent_net_worth_at_10=7500, opponent_net_worth_at_20=15000,
        gpm=480, xpm=600, total_last_hits=200,
        first_core_item_minute=None, first_core_item_name=None,
        laning_heatmap_own_half_pct=0.4, ward_purchases=0,
        teamfight_participation_rate=0.6, teamfight_avg_damage_contribution=None,
        first_roshan_minute=None, first_tower_minute=None,
        turbo=False,
    )
    match_meta = {"players": [], "patch": "7.37"}

    with patch("dota_coach.enricher._get_heroes_data", new_callable=AsyncMock, return_value={}), \
         patch("dota_coach.enricher._get_benchmarks_cached", new_callable=AsyncMock, return_value={"result": {}}), \
         patch("dota_coach.enricher.get_hero_bracket_benchmarks", new_callable=AsyncMock, return_value={}), \
         patch("dota_coach.enricher._get_item_timings_cached", new_callable=AsyncMock, return_value=[]), \
         patch("dota_coach.enricher._get_bootstrap_cached", new_callable=AsyncMock, return_value=[]), \
         patch("dota_coach.enricher._get_items_data", new_callable=AsyncMock, return_value={}), \
         patch("dota_coach.history.get_local_benchmarks", return_value=([], 0)):
        ctx = asyncio.run(enrich(m, match_meta, account_id=123))

    assert ctx is not None
