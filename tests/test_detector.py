"""Unit tests for all threshold rules in detector.py (v1 + v2 percentile)."""
from __future__ import annotations

import pytest

from dota_coach.detector import SEVERITY_THRESHOLDS, detect_errors
from dota_coach.models import (
    DetectedError,
    EnrichmentContext,
    HeroBenchmark,
    ItemBootstrapEntry,
    MatchMetrics,
    RoleProfile,
)


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
        opponent_net_worth_at_10=3200,
        net_worth_at_20=8000,
        opponent_net_worth_at_20=7000,
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
    errors = detect_errors(_base_metrics(net_worth_at_10=2000, opponent_net_worth_at_10=3500))
    assert "Net worth deficit at 10" in _categories(errors)


def test_rule5_does_not_fire_at_exact_1000_deficit():
    errors = detect_errors(_base_metrics(net_worth_at_10=2500, opponent_net_worth_at_10=3500))
    assert "Net worth deficit at 10" not in _categories(errors)


def test_rule5_does_not_fire_when_ahead():
    errors = detect_errors(_base_metrics(net_worth_at_10=4000, opponent_net_worth_at_10=3000))
    assert "Net worth deficit at 10" not in _categories(errors)


def test_rule5_severity_is_high():
    errors = detect_errors(_base_metrics(net_worth_at_10=2000, opponent_net_worth_at_10=3500))
    rule = next(e for e in errors if e.category == "Net worth deficit at 10")
    assert rule.severity == "high"


# ---------------------------------------------------------------------------
# Rule 6: Net worth deficit at 20 (> 2500g)
# ---------------------------------------------------------------------------

def test_rule6_fires_when_deficit_exceeds_2500():
    errors = detect_errors(_base_metrics(net_worth_at_20=5000, opponent_net_worth_at_20=8000))
    assert "Net worth deficit at 20" in _categories(errors)


def test_rule6_does_not_fire_at_exact_2500_deficit():
    errors = detect_errors(_base_metrics(net_worth_at_20=5500, opponent_net_worth_at_20=8000))
    assert "Net worth deficit at 20" not in _categories(errors)


def test_rule6_severity_is_critical():
    errors = detect_errors(_base_metrics(net_worth_at_20=5000, opponent_net_worth_at_20=8000))
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
        opponent_net_worth_at_10=3000,
        laning_heatmap_own_half_pct=0.80,
        ward_purchases=3,
    ))
    assert len(errors) <= 3


# ===========================================================================
# v2 percentile-based tests
# ===========================================================================

def _carry_profile() -> RoleProfile:
    return RoleProfile(
        observed_metrics=["gpm", "lh_at_10", "teamfight_participation"],
        death_limit_before_10=2,
        tf_participation_limit=0.40,
        ward_rule="flag_if_laning_phase",
    )


def _support_profile() -> RoleProfile:
    return RoleProfile(
        observed_metrics=["ward_placements", "teamfight_participation"],
        death_limit_before_10=3,
        tf_participation_limit=0.55,
        ward_rule="require_minimum",
    )


def _enrichment(gpm_pct: float = 0.5, xpm_pct: float = 0.5, lh_pct: float = 0.5) -> EnrichmentContext:
    return EnrichmentContext(
        patch_name="7.38c",
        benchmarks=[
            HeroBenchmark(metric="gold_per_min", player_value=400, player_pct=gpm_pct, bracket_avg=420),
            HeroBenchmark(metric="xp_per_min", player_value=500, player_pct=xpm_pct, bracket_avg=500),
            HeroBenchmark(metric="last_hits_per_min", player_value=5.0, player_pct=lh_pct, bracket_avg=5.5),
        ],
        item_costs={},
        hero_base_stats={},
    )


def test_pct_critical_when_below_20():
    errors = detect_errors(
        _base_metrics(),
        role_profile=_carry_profile(),
        enrichment=_enrichment(gpm_pct=0.15),
    )
    cats = _categories(errors)
    assert "Low GPM" in cats
    gpm_err = next(e for e in errors if e.category == "Low GPM")
    assert gpm_err.severity == "critical"
    assert gpm_err.player_pct == 0.15


def test_pct_high_when_between_20_and_35():
    errors = detect_errors(
        _base_metrics(),
        role_profile=_carry_profile(),
        enrichment=_enrichment(gpm_pct=0.25),
    )
    gpm_err = next(e for e in errors if e.category == "Low GPM")
    assert gpm_err.severity == "high"


def test_pct_medium_when_between_35_and_45():
    errors = detect_errors(
        _base_metrics(),
        role_profile=_carry_profile(),
        enrichment=_enrichment(gpm_pct=0.40),
    )
    gpm_err = next(e for e in errors if e.category == "Low GPM")
    assert gpm_err.severity == "medium"


def test_pct_no_error_when_above_45():
    errors = detect_errors(
        _base_metrics(),
        role_profile=_carry_profile(),
        enrichment=_enrichment(gpm_pct=0.50),
    )
    cats = _categories(errors)
    assert "Low GPM" not in cats


def test_role_specific_death_limit():
    """Offlaner with death_limit=3 should not trigger at 3 deaths."""
    offlaner = RoleProfile(
        observed_metrics=["gpm"],
        death_limit_before_10=3,
        tf_participation_limit=0.50,
        ward_rule="none",
    )
    errors = detect_errors(
        _base_metrics(deaths_before_10=3, death_timestamps_laning=[2.0, 5.0, 8.0]),
        role_profile=offlaner,
        enrichment=_enrichment(),
    )
    assert "Unsafe laning" not in _categories(errors)


def test_role_specific_death_limit_fires_at_4():
    offlaner = RoleProfile(
        observed_metrics=["gpm"],
        death_limit_before_10=3,
        tf_participation_limit=0.50,
        ward_rule="none",
    )
    errors = detect_errors(
        _base_metrics(deaths_before_10=4, death_timestamps_laning=[2.0, 5.0, 8.0, 9.0]),
        role_profile=offlaner,
        enrichment=_enrichment(),
    )
    assert "Unsafe laning" in _categories(errors)


def test_support_low_wards_fires():
    errors = detect_errors(
        _base_metrics(ward_placements=5),
        role_profile=_support_profile(),
        enrichment=_enrichment(),
    )
    assert "Low ward output" in _categories(errors)


def test_support_adequate_wards_no_error():
    errors = detect_errors(
        _base_metrics(ward_placements=10),
        role_profile=_support_profile(),
        enrichment=_enrichment(),
    )
    assert "Low ward output" not in _categories(errors)


def test_v2_no_absolute_laning_rules_when_enrichment_given():
    """When enrichment is provided, v1 absolute rules (Poor laning CS, etc.) should NOT fire."""
    errors = detect_errors(
        _base_metrics(lh_at_10=20, laning_heatmap_own_half_pct=0.90),
        role_profile=_carry_profile(),
        enrichment=_enrichment(gpm_pct=0.50, lh_pct=0.50),
    )
    cats = _categories(errors)
    assert "Poor laning CS" not in cats
    assert "Passive laning" not in cats


def test_v2_top3_limit_with_percentile_errors():
    """Even with many percentile errors, at most 3 returned."""
    errors = detect_errors(
        _base_metrics(deaths_before_10=4, death_timestamps_laning=[1.0, 3.0, 5.0, 8.0]),
        role_profile=_carry_profile(),
        enrichment=_enrichment(gpm_pct=0.10, lh_pct=0.10),
    )
    assert len(errors) <= 3


# ---------------------------------------------------------------------------
# detect_errors item timing (bootstrap-driven)
# ---------------------------------------------------------------------------

def _bootstrap_entry(item_name: str, avg_time_minutes: float, match_frequency: float = 0.60) -> ItemBootstrapEntry:
    return ItemBootstrapEntry(
        item_id=1,
        item_name=item_name,
        match_frequency=match_frequency,
        win_rate=0.55,
        avg_time_minutes=avg_time_minutes,
    )


def _enrichment_with_bootstrap(bootstrap: list[ItemBootstrapEntry]) -> EnrichmentContext:
    return EnrichmentContext(
        patch_name="7.38",
        benchmarks=[],
        item_costs={},
        hero_base_stats={},
        hero_item_bootstrap=bootstrap,
    )


class TestDetectErrorsItemTiming:
    """Integration tests: detect_errors() slow item timing rule (bootstrap-driven)."""

    _TIMING_WINDOW = 4  # matches detector constant

    def test_no_error_when_on_time(self):
        # Arrived exactly at avg — no overshoot
        bootstrap = [_bootstrap_entry("battle_fury", 17.0)]
        errors = detect_errors(
            _base_metrics(
                hero="Anti-Mage",
                first_core_item_name="item_battle_fury",
                first_core_item_minute=17.0 + self._TIMING_WINDOW - 0.5,  # just inside window
            ),
            enrichment=_enrichment_with_bootstrap(bootstrap),
        )
        assert all(e.category != "Slow item timing" for e in errors)

    def test_high_severity_when_moderately_late(self):
        bootstrap = [_bootstrap_entry("battle_fury", 17.0)]
        errors = detect_errors(
            _base_metrics(
                hero="Anti-Mage",
                first_core_item_name="item_battle_fury",
                first_core_item_minute=17.0 + self._TIMING_WINDOW + 1.0,  # just outside window
            ),
            enrichment=_enrichment_with_bootstrap(bootstrap),
        )
        timing_errors = [e for e in errors if e.category == "Slow item timing"]
        assert len(timing_errors) == 1
        assert timing_errors[0].severity == "high"

    def test_critical_severity_when_very_late(self):
        bootstrap = [_bootstrap_entry("battle_fury", 17.0)]
        errors = detect_errors(
            _base_metrics(
                hero="Anti-Mage",
                first_core_item_name="item_battle_fury",
                first_core_item_minute=17.0 + self._TIMING_WINDOW * 2 + 1.0,
            ),
            enrichment=_enrichment_with_bootstrap(bootstrap),
        )
        timing_errors = [e for e in errors if e.category == "Slow item timing"]
        assert len(timing_errors) == 1
        assert timing_errors[0].severity == "critical"

    def test_no_error_for_non_standard_item_sets_build_note(self):
        # bootstrap has battle_fury but player bought desolator — non-standard
        bootstrap = [_bootstrap_entry("battle_fury", 17.0)]
        enrichment = _enrichment_with_bootstrap(bootstrap)
        errors = detect_errors(
            _base_metrics(
                hero="Anti-Mage",
                first_core_item_name="item_desolator",
                first_core_item_minute=20.0,
            ),
            enrichment=enrichment,
        )
        assert all(e.category != "Slow item timing" for e in errors)
        assert enrichment.build_note is not None
        assert "Non-standard" in enrichment.build_note

    def test_fallback_slow_core_item_when_bootstrap_empty(self):
        """When bootstrap is empty, falls back to config.SLOW_CORE_ITEM_MINUTES."""
        enrichment = _enrichment_with_bootstrap([])
        errors = detect_errors(
            _base_metrics(
                hero="Anti-Mage",
                first_core_item_name="item_battle_fury",
                first_core_item_minute=99.0,  # absurdly late
            ),
            enrichment=enrichment,
        )
        slow_errors = [e for e in errors if e.category == "Slow core item"]
        assert len(slow_errors) == 1

    def test_no_error_without_enrichment(self):
        """Item timing check is skipped entirely in v1 mode (no enrichment)."""
        errors = detect_errors(
            _base_metrics(
                hero="Anti-Mage",
                first_core_item_name="item_battle_fury",
                first_core_item_minute=99.0,  # absurdly late
            ),
        )
        assert all(e.category != "Slow item timing" for e in errors)


from dota_coach.models import LocalBenchmark


def _make_enrichment_with_local(global_pct: float, local_pct: float):
    """Build an EnrichmentContext with one global and one local benchmark for gold_per_min."""
    from dota_coach.models import EnrichmentContext, HeroBenchmark, LocalBenchmark
    return EnrichmentContext(
        patch_name="7.37",
        benchmarks=[
            HeroBenchmark(metric="gold_per_min", player_value=400.0,
                          player_pct=global_pct, bracket_avg=480.0)
        ],
        item_costs={},
        hero_base_stats={},
        local_benchmarks=[
            LocalBenchmark(metric="gold_per_min", player_value=400.0,
                           player_pct=local_pct, p25=350.0, median=480.0,
                           p75=560.0, sample_size=40)
        ],
    )


class TestLocalBenchmarkDetection:
    def _base_metrics(self):
        from dota_coach.models import MatchMetrics
        return MatchMetrics(
            match_id=1, hero="Anti-Mage", duration_minutes=35.0, result="loss",
            lh_at_10=60, denies_at_10=5, deaths_before_10=0,
            death_timestamps_laning=[], net_worth_at_10=8000, net_worth_at_20=16000,
            opponent_net_worth_at_10=7500, opponent_net_worth_at_20=15000,
            gpm=400, xpm=550, total_last_hits=180,
            first_core_item_minute=None, first_core_item_name=None,
            laning_heatmap_own_half_pct=0.4, ward_purchases=0,
            teamfight_participation_rate=0.6, teamfight_avg_damage_contribution=None,
            first_roshan_minute=None, first_tower_minute=None, turbo=False,
        )

    def _base_role_profile(self):
        from dota_coach.models import RoleProfile
        return RoleProfile(
            observed_metrics=["gpm"],
            death_limit_before_10=2,
            tf_participation_limit=0.4,
            ward_rule="flag_if_laning_phase",
        )

    def test_both_sources_below_threshold_produces_single_merged_error(self):
        """When both global and local percentiles are below threshold, one error is produced."""
        enr = _make_enrichment_with_local(global_pct=0.18, local_pct=0.22)
        errors = detect_errors(self._base_metrics(), self._base_role_profile(), enr)
        gpm_errors = [e for e in errors if "GPM" in e.category]
        assert len(gpm_errors) == 1
        # Context must mention both global and local
        assert "global" in gpm_errors[0].context.lower()
        assert "game" in gpm_errors[0].context.lower() or "sample" in gpm_errors[0].context.lower()

    def test_only_local_below_threshold_fires_error(self):
        """When global is fine but local is below threshold, error still fires."""
        enr = _make_enrichment_with_local(global_pct=0.52, local_pct=0.18)
        errors = detect_errors(self._base_metrics(), self._base_role_profile(), enr)
        gpm_errors = [e for e in errors if "GPM" in e.category]
        assert len(gpm_errors) == 1

    def test_both_above_threshold_no_error(self):
        """When both global and local are above threshold, no error fires."""
        enr = _make_enrichment_with_local(global_pct=0.55, local_pct=0.60)
        errors = detect_errors(self._base_metrics(), self._base_role_profile(), enr)
        gpm_errors = [e for e in errors if "GPM" in e.category]
        assert len(gpm_errors) == 0
