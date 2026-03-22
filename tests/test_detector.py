"""Unit tests for all 10 threshold rules in detector.py."""
from __future__ import annotations

import pytest

from dota_coach.detector import detect_errors
from dota_coach.models import MatchMetrics


def _base_metrics(**overrides) -> MatchMetrics:
    """Return a MatchMetrics with no errors triggered (all values pass thresholds)."""
    defaults = dict(
        match_id=1,
        hero="DrowRanger",
        duration_minutes=35.0,
        result="win",
        lh_at_10=50,
        denies_at_10=5,
        deaths_before_10=0,
        death_timestamps_laning=[],
        net_worth_at_10=3500,
        enemy_carry_net_worth_at_10=3200,
        net_worth_at_20=8000,
        enemy_carry_net_worth_at_20=7000,
        gpm=400,
        xpm=500,
        first_core_item_minute=15.0,
        first_core_item_name="item_battle_fury",
        laning_heatmap_own_half_pct=0.60,
        ward_purchases=0,
        teamfight_participation_rate=0.55,
        teamfight_avg_damage_contribution=0.20,
        first_roshan_minute=None,
        first_tower_minute=None,
    )
    defaults.update(overrides)
    return MatchMetrics(**defaults)


def _categories(errors) -> list[str]:
    return [e.category for e in errors]


# ---------------------------------------------------------------------------
# Rule 1: Poor laning CS
# ---------------------------------------------------------------------------

def test_rule1_fires_when_lh_below_threshold():
    errors = detect_errors(_base_metrics(lh_at_10=44))
    assert "Poor laning CS" in _categories(errors)


def test_rule1_does_not_fire_at_threshold():
    errors = detect_errors(_base_metrics(lh_at_10=45))
    assert "Poor laning CS" not in _categories(errors)


def test_rule1_severity_is_high():
    errors = detect_errors(_base_metrics(lh_at_10=30))
    rule = next(e for e in errors if e.category == "Poor laning CS")
    assert rule.severity == "high"


# ---------------------------------------------------------------------------
# Rule 2: Unsafe laning (> 2 deaths)
# ---------------------------------------------------------------------------

def test_rule2_fires_when_more_than_two_deaths():
    errors = detect_errors(_base_metrics(
        deaths_before_10=3,
        death_timestamps_laning=[2.0, 5.0, 8.0],
    ))
    assert "Unsafe laning" in _categories(errors)


def test_rule2_does_not_fire_at_two_deaths():
    errors = detect_errors(_base_metrics(
        deaths_before_10=2,
        death_timestamps_laning=[4.0, 7.0],
    ))
    assert "Unsafe laning" not in _categories(errors)


def test_rule2_severity_is_critical():
    errors = detect_errors(_base_metrics(
        deaths_before_10=3,
        death_timestamps_laning=[2.0, 5.0, 8.0],
    ))
    rule = next(e for e in errors if e.category == "Unsafe laning")
    assert rule.severity == "critical"


# ---------------------------------------------------------------------------
# Rule 3: Single early death (before 5 min)
# ---------------------------------------------------------------------------

def test_rule3_fires_when_single_death_before_5_min():
    errors = detect_errors(_base_metrics(
        deaths_before_10=1,
        death_timestamps_laning=[3.5],
    ))
    assert "Single early death" in _categories(errors)


def test_rule3_does_not_fire_when_single_death_after_5_min():
    errors = detect_errors(_base_metrics(
        deaths_before_10=1,
        death_timestamps_laning=[6.0],
    ))
    assert "Single early death" not in _categories(errors)


def test_rule3_does_not_fire_with_two_deaths_early():
    # Rule 3 only applies when deaths_before_10 == 1 exactly
    errors = detect_errors(_base_metrics(
        deaths_before_10=2,
        death_timestamps_laning=[2.0, 4.0],
    ))
    assert "Single early death" not in _categories(errors)


def test_rule3_severity_is_high():
    errors = detect_errors(_base_metrics(
        deaths_before_10=1,
        death_timestamps_laning=[3.5],
    ))
    rule = next(e for e in errors if e.category == "Single early death")
    assert rule.severity == "high"


# ---------------------------------------------------------------------------
# Rule 4: Slow core item (> 18 min)
# ---------------------------------------------------------------------------

def test_rule4_fires_when_first_core_after_18_min():
    errors = detect_errors(_base_metrics(first_core_item_minute=19.0, first_core_item_name="item_manta"))
    assert "Slow core item" in _categories(errors)


def test_rule4_does_not_fire_at_18_min():
    errors = detect_errors(_base_metrics(first_core_item_minute=18.0))
    assert "Slow core item" not in _categories(errors)


def test_rule4_does_not_fire_when_no_core_item():
    errors = detect_errors(_base_metrics(first_core_item_minute=None, first_core_item_name=None))
    assert "Slow core item" not in _categories(errors)


def test_rule4_severity_is_high():
    errors = detect_errors(_base_metrics(first_core_item_minute=22.0, first_core_item_name="item_manta"))
    rule = next(e for e in errors if e.category == "Slow core item")
    assert rule.severity == "high"


# ---------------------------------------------------------------------------
# Rule 5: Net worth deficit at 10 (> 1000g)
# ---------------------------------------------------------------------------

def test_rule5_fires_when_deficit_exceeds_1000():
    errors = detect_errors(_base_metrics(net_worth_at_10=2000, enemy_carry_net_worth_at_10=3500))
    assert "Net worth deficit at 10" in _categories(errors)


def test_rule5_does_not_fire_at_exact_1000_deficit():
    errors = detect_errors(_base_metrics(net_worth_at_10=2500, enemy_carry_net_worth_at_10=3500))
    assert "Net worth deficit at 10" not in _categories(errors)


def test_rule5_does_not_fire_when_ahead():
    errors = detect_errors(_base_metrics(net_worth_at_10=4000, enemy_carry_net_worth_at_10=3000))
    assert "Net worth deficit at 10" not in _categories(errors)


def test_rule5_severity_is_high():
    errors = detect_errors(_base_metrics(net_worth_at_10=2000, enemy_carry_net_worth_at_10=3500))
    rule = next(e for e in errors if e.category == "Net worth deficit at 10")
    assert rule.severity == "high"


# ---------------------------------------------------------------------------
# Rule 6: Net worth deficit at 20 (> 2500g)
# ---------------------------------------------------------------------------

def test_rule6_fires_when_deficit_exceeds_2500():
    errors = detect_errors(_base_metrics(net_worth_at_20=5000, enemy_carry_net_worth_at_20=8000))
    assert "Net worth deficit at 20" in _categories(errors)


def test_rule6_does_not_fire_at_exact_2500_deficit():
    errors = detect_errors(_base_metrics(net_worth_at_20=5500, enemy_carry_net_worth_at_20=8000))
    assert "Net worth deficit at 20" not in _categories(errors)


def test_rule6_severity_is_critical():
    errors = detect_errors(_base_metrics(net_worth_at_20=5000, enemy_carry_net_worth_at_20=8000))
    rule = next(e for e in errors if e.category == "Net worth deficit at 20")
    assert rule.severity == "critical"


# ---------------------------------------------------------------------------
# Rule 7: Passive laning (> 70% own half)
# ---------------------------------------------------------------------------

def test_rule7_fires_when_own_half_pct_exceeds_threshold():
    errors = detect_errors(_base_metrics(laning_heatmap_own_half_pct=0.75))
    assert "Passive laning" in _categories(errors)


def test_rule7_does_not_fire_at_70_pct():
    errors = detect_errors(_base_metrics(laning_heatmap_own_half_pct=0.70))
    assert "Passive laning" not in _categories(errors)


def test_rule7_severity_is_medium():
    errors = detect_errors(_base_metrics(laning_heatmap_own_half_pct=0.80))
    rule = next(e for e in errors if e.category == "Passive laning")
    assert rule.severity == "medium"


# ---------------------------------------------------------------------------
# Rule 8: Carry buying wards (>= 2)
# ---------------------------------------------------------------------------

def test_rule8_fires_when_ward_purchases_two_or_more():
    errors = detect_errors(_base_metrics(ward_purchases=2))
    assert "Carry buying wards" in _categories(errors)


def test_rule8_does_not_fire_with_one_ward_purchase():
    errors = detect_errors(_base_metrics(ward_purchases=1))
    assert "Carry buying wards" not in _categories(errors)


def test_rule8_severity_is_medium():
    errors = detect_errors(_base_metrics(ward_purchases=3))
    rule = next(e for e in errors if e.category == "Carry buying wards")
    assert rule.severity == "medium"


# ---------------------------------------------------------------------------
# Rule 9: Farming during fights (< 40% participation)
# ---------------------------------------------------------------------------

def test_rule9_fires_when_participation_below_threshold():
    errors = detect_errors(_base_metrics(teamfight_participation_rate=0.35))
    assert "Farming during fights" in _categories(errors)


def test_rule9_does_not_fire_at_threshold():
    errors = detect_errors(_base_metrics(teamfight_participation_rate=0.40))
    assert "Farming during fights" not in _categories(errors)


def test_rule9_does_not_fire_when_none():
    errors = detect_errors(_base_metrics(teamfight_participation_rate=None))
    assert "Farming during fights" not in _categories(errors)


def test_rule9_severity_is_medium():
    errors = detect_errors(_base_metrics(teamfight_participation_rate=0.20))
    rule = next(e for e in errors if e.category == "Farming during fights")
    assert rule.severity == "medium"


# ---------------------------------------------------------------------------
# Ordering: critical > high > medium
# ---------------------------------------------------------------------------

def test_errors_sorted_critical_first():
    # Trigger both critical and medium rules
    errors = detect_errors(_base_metrics(
        deaths_before_10=3,
        death_timestamps_laning=[2.0, 5.0, 8.0],  # critical: unsafe laning
        ward_purchases=2,                           # medium: carry buying wards
        teamfight_participation_rate=0.30,          # medium: farming during fights
    ))
    assert errors[0].severity == "critical"
    for i in range(len(errors) - 1):
        sev = {"critical": 0, "high": 1, "medium": 2}
        assert sev[errors[i].severity] <= sev[errors[i + 1].severity]


# ---------------------------------------------------------------------------
# No errors when all thresholds pass
# ---------------------------------------------------------------------------

def test_no_errors_for_clean_game():
    errors = detect_errors(_base_metrics())
    assert errors == []


# ---------------------------------------------------------------------------
# Top 3 limit: at most 3 errors returned even when more fire
# ---------------------------------------------------------------------------

def test_at_most_three_errors_returned():
    # Trigger 5 rules simultaneously:
    # Rule 1 (high): lh < 45
    # Rule 2 (critical): deaths > 2
    # Rule 5 (high): nw deficit > 1000 at 10
    # Rule 7 (medium): own-half > 70%
    # Rule 8 (medium): ward purchases >= 2
    errors = detect_errors(_base_metrics(
        lh_at_10=20,
        deaths_before_10=3,
        death_timestamps_laning=[2.0, 5.0, 8.0],
        net_worth_at_10=1000,
        enemy_carry_net_worth_at_10=3000,
        laning_heatmap_own_half_pct=0.80,
        ward_purchases=3,
    ))
    assert len(errors) <= 3
