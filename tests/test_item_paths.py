"""Tests for bootstrap-driven item path resolution and timing detection."""
from __future__ import annotations

import pytest

from dota_coach.enricher import _resolve_bootstrap_entries
from dota_coach.models import EnrichmentContext, ItemBootstrapEntry, MatchMetrics
from dota_coach.detector import detect_errors


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _raw_entry(item_id: int, match_count: int, win_count: int, avg_time_sec: int) -> dict:
    return {"itemId": item_id, "matchCount": match_count, "winCount": win_count, "avgTime": avg_time_sec}


def _items_data_with(*items: tuple[str, int]) -> dict:
    """Build a minimal items_data dict mapping name -> {id: numeric_id}."""
    return {name: {"id": item_id, "qual": "artifact", "cost": 4000} for name, item_id in items}


def _base_metrics(**overrides) -> MatchMetrics:
    defaults = dict(
        match_id=1, hero="Anti-Mage", duration_minutes=35.0, result="win",
        lh_at_10=50, denies_at_10=5, deaths_before_10=0, death_timestamps_laning=[],
        net_worth_at_10=3500, enemy_carry_net_worth_at_10=3200,
        net_worth_at_20=8000, enemy_carry_net_worth_at_20=7000,
        gpm=400, xpm=500, first_core_item_minute=17.0,
        first_core_item_name="item_battle_fury",
        laning_heatmap_own_half_pct=0.55, ward_purchases=0,
        teamfight_participation_rate=0.55, teamfight_avg_damage_contribution=0.20,
        first_roshan_minute=None, first_tower_minute=None,
    )
    defaults.update(overrides)
    return MatchMetrics(**defaults)


def _enrichment(bootstrap: list[ItemBootstrapEntry]) -> EnrichmentContext:
    return EnrichmentContext(
        patch_name="7.38", benchmarks=[], item_costs={}, hero_base_stats={},
        hero_item_bootstrap=bootstrap,
    )


# ---------------------------------------------------------------------------
# Unit tests for _resolve_bootstrap_entries
# ---------------------------------------------------------------------------

def test_empty_raw_returns_empty():
    assert _resolve_bootstrap_entries([], {}) == []


def test_zero_total_matches_returns_empty():
    raw = [_raw_entry(11, 0, 0, 900), _raw_entry(12, 0, 0, 1200)]
    items = _items_data_with(("bfury", 11), ("radiance", 12))
    assert _resolve_bootstrap_entries(raw, items) == []


def test_filters_below_frequency_threshold():
    # 1 entry with 10% frequency (10 out of 100 total matches) — below 0.15
    raw = [_raw_entry(11, 10, 5, 900), _raw_entry(99, 90, 45, 600)]
    items = _items_data_with(("bfury", 11), ("other", 99))
    result = _resolve_bootstrap_entries(raw, items)
    assert all(e.item_name != "bfury" for e in result)


def test_resolves_item_name_from_items_data():
    raw = [_raw_entry(11, 100, 55, 900)]
    items = _items_data_with(("bfury", 11))
    result = _resolve_bootstrap_entries(raw, items)
    assert len(result) == 1
    assert result[0].item_name == "bfury"
    assert result[0].item_id == 11


def test_skips_unknown_item_id():
    raw = [_raw_entry(999, 200, 100, 900)]
    items = _items_data_with(("bfury", 11))  # 999 not in items_data
    result = _resolve_bootstrap_entries(raw, items)
    assert result == []


def test_converts_avg_time_to_minutes():
    raw = [_raw_entry(11, 100, 55, 1020)]
    items = _items_data_with(("bfury", 11))
    result = _resolve_bootstrap_entries(raw, items)
    assert len(result) == 1
    assert result[0].avg_time_minutes == pytest.approx(17.0)


def test_computes_win_rate():
    raw = [_raw_entry(11, 100, 60, 900)]
    items = _items_data_with(("bfury", 11))
    result = _resolve_bootstrap_entries(raw, items)
    assert len(result) == 1
    assert result[0].win_rate == pytest.approx(0.60)


def test_computes_match_frequency():
    # total = 500; entry 1: 400/500 = 0.8, entry 2: 100/500 = 0.2
    raw = [_raw_entry(11, 400, 200, 900), _raw_entry(12, 100, 50, 1200)]
    items = _items_data_with(("bfury", 11), ("radiance", 12))
    result = _resolve_bootstrap_entries(raw, items)
    by_name = {e.item_name: e for e in result}
    assert by_name["bfury"].match_frequency == pytest.approx(0.8)
    assert by_name["radiance"].match_frequency == pytest.approx(0.2)


def test_multiple_entries_all_above_threshold():
    # 3 entries totalling 1000 matches; each ~333 = 33.3% — all above 0.15
    raw = [
        _raw_entry(11, 340, 170, 900),
        _raw_entry(12, 330, 165, 1200),
        _raw_entry(13, 330, 165, 1500),
    ]
    items = _items_data_with(("bfury", 11), ("radiance", 12), ("manta", 13))
    result = _resolve_bootstrap_entries(raw, items)
    assert len(result) == 3


# ---------------------------------------------------------------------------
# Integration tests with detect_errors
# ---------------------------------------------------------------------------

def test_item_found_on_time_no_error():
    bootstrap = [ItemBootstrapEntry(
        item_id=11, item_name="battle_fury", match_frequency=0.60,
        win_rate=0.55, avg_time_minutes=17.0,
    )]
    # Player buys at 20.0 — overshoot is 3 min, within 4 min window
    metrics = _base_metrics(first_core_item_minute=20.0, first_core_item_name="item_battle_fury")
    enrichment = _enrichment(bootstrap)
    errors = detect_errors(metrics, enrichment=enrichment)
    assert not any(e.category == "Slow item timing" for e in errors)


def test_item_found_late_high_severity():
    bootstrap = [ItemBootstrapEntry(
        item_id=11, item_name="battle_fury", match_frequency=0.60,
        win_rate=0.55, avg_time_minutes=17.0,
    )]
    # Player buys at 22.0 — overshoot is 5 min > 4, but ≤ 8 → "high"
    metrics = _base_metrics(first_core_item_minute=22.0, first_core_item_name="item_battle_fury")
    enrichment = _enrichment(bootstrap)
    errors = detect_errors(metrics, enrichment=enrichment)
    timing_errors = [e for e in errors if e.category == "Slow item timing"]
    assert len(timing_errors) == 1
    assert timing_errors[0].severity == "high"


def test_item_found_very_late_critical():
    bootstrap = [ItemBootstrapEntry(
        item_id=11, item_name="battle_fury", match_frequency=0.60,
        win_rate=0.55, avg_time_minutes=17.0,
    )]
    # Player buys at 30.0 — overshoot is 13 min > 8 (2×window) → "critical"
    metrics = _base_metrics(first_core_item_minute=30.0, first_core_item_name="item_battle_fury")
    enrichment = _enrichment(bootstrap)
    errors = detect_errors(metrics, enrichment=enrichment)
    timing_errors = [e for e in errors if e.category == "Slow item timing"]
    assert len(timing_errors) == 1
    assert timing_errors[0].severity == "critical"


def test_item_not_in_bootstrap_sets_build_note():
    # Bootstrap has bfury only; player buys desolator
    bootstrap = [ItemBootstrapEntry(
        item_id=11, item_name="bfury", match_frequency=0.60,
        win_rate=0.55, avg_time_minutes=17.0,
    )]
    metrics = _base_metrics(first_core_item_minute=17.0, first_core_item_name="item_desolator")
    enrichment = _enrichment(bootstrap)
    errors = detect_errors(metrics, enrichment=enrichment)
    # No "Slow item timing" error for non-standard item
    assert not any(e.category == "Slow item timing" for e in errors)
    # But build_note is set and contains "Non-standard"
    assert enrichment.build_note is not None
    assert "Non-standard" in enrichment.build_note


def test_empty_bootstrap_uses_universal_fallback():
    # Empty bootstrap — falls back to config.SLOW_CORE_ITEM_MINUTES
    from dota_coach import config
    metrics = _base_metrics(
        first_core_item_minute=99.0,
        first_core_item_name="item_battle_fury",
    )
    enrichment = _enrichment([])
    errors = detect_errors(metrics, enrichment=enrichment)
    slow_errors = [e for e in errors if e.category == "Slow core item"]
    assert len(slow_errors) == 1
