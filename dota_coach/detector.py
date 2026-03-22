"""Carry mistake detection with threshold rules (v2: percentile-based, role-aware)."""
from __future__ import annotations

from typing import Optional

from dota_coach import config
from dota_coach.models import (
    DetectedError,
    EnrichmentContext,
    HeroBenchmark,
    MatchMetrics,
    RoleProfile,
)

CORE_ITEMS: frozenset[str] = frozenset({
    "item_battle_fury",
    "item_manta",
    "item_black_king_bar",
    "item_maelstrom",
    "item_mjollnir",
    "item_desolator",
    "item_butterfly",
    "item_greater_crit",
    "item_skadi",
    "item_sange_and_yasha",
    "item_yasha",
    "item_monkey_king_bar",
    "item_hand_of_midas",
    "item_ancient_janggo",
    "item_ultimate_scepter",
    "item_diffusal_blade",
    "item_radiance",
    "item_armlet",
    "item_helm_of_the_dominator",
})

_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2}

# v2 percentile severity thresholds (PRD 4.1)
SEVERITY_THRESHOLDS = {
    "critical": 0.20,
    "high": 0.35,
    "medium": 0.45,
}


def _pct_severity(pct: float) -> str | None:
    """Map a percentile (0-1) to severity. Returns None if above all thresholds."""
    if pct < SEVERITY_THRESHOLDS["critical"]:
        return "critical"
    elif pct < SEVERITY_THRESHOLDS["high"]:
        return "high"
    elif pct < SEVERITY_THRESHOLDS["medium"]:
        return "medium"
    return None


def _find_benchmark(benchmarks: list[HeroBenchmark], metric: str) -> HeroBenchmark | None:
    """Find a benchmark entry by metric name."""
    return next((b for b in benchmarks if b.metric == metric), None)


# Mapping from RoleProfile.observed_metrics entries to benchmark metric keys
_METRIC_TO_BENCH = {
    "gpm": "gold_per_min",
    "lh_at_10": "last_hits_per_min",
    "teamfight_participation": None,  # not in benchmarks, handled by base rules
    "first_core_minute": None,        # not in benchmarks
    "net_worth_delta_10": None,       # not in benchmarks
    "net_worth_delta_20": None,       # not in benchmarks
    "laning_heatmap_own_half_pct": None,  # not in benchmarks
    "ward_placements": None,          # not in benchmarks
}

# Human-readable metric labels
_METRIC_LABELS = {
    "gold_per_min": "GPM",
    "xp_per_min": "XPM",
    "last_hits_per_min": "LH/min",
}


def detect_errors(
    metrics: MatchMetrics,
    role_profile: Optional[RoleProfile] = None,
    enrichment: Optional[EnrichmentContext] = None,
) -> list[DetectedError]:
    """Apply detection rules and return errors sorted by severity (top 3).

    v1 mode: call with just metrics — uses hardcoded carry thresholds.
    v2 mode: provide role_profile and enrichment for percentile-based detection.
    """
    errors: list[DetectedError] = []

    # --- Determine role-specific parameters ---
    death_limit = role_profile.death_limit_before_10 if role_profile else config.DEATH_LIMIT_BEFORE_10
    tf_floor = role_profile.tf_participation_limit if role_profile else config.TF_PARTICIPATION_FLOOR
    ward_rule = role_profile.ward_rule if role_profile else "flag_if_laning_phase"

    # ===================================================================
    # BASE RULES (all roles, absolute thresholds)
    # ===================================================================

    # B1: Unsafe laning — multiple deaths
    if metrics.deaths_before_10 > death_limit:
        errors.append(DetectedError(
            category="Unsafe laning",
            description=f"Died more than {death_limit} times before 10 minutes",
            severity="critical",
            metric_value=f"{metrics.deaths_before_10} deaths before 10:00",
            threshold=f"> {death_limit} deaths is unsafe laning",
        ))

    # B2: Very early death
    if (
        metrics.deaths_before_10 >= 1
        and metrics.death_timestamps_laning
        and metrics.death_timestamps_laning[0] < config.EARLY_DEATH_MINUTES
        and metrics.deaths_before_10 == 1
    ):
        errors.append(DetectedError(
            category="Single early death",
            description=f"Died before {config.EARLY_DEATH_MINUTES:.0f} minutes — likely overextension or missing TP",
            severity="high",
            metric_value=f"Died at {metrics.death_timestamps_laning[0]:.1f} min",
            threshold=f"Death before {config.EARLY_DEATH_MINUTES:.0f}:00 is addressable",
        ))

    # B3: Farming during fights
    if metrics.teamfight_participation_rate is not None and metrics.teamfight_participation_rate < tf_floor:
        errors.append(DetectedError(
            category="Farming during fights",
            description=f"Participated in fewer than {tf_floor:.0%} of teamfights",
            severity="medium",
            metric_value=f"{metrics.teamfight_participation_rate:.0%} teamfight participation",
            threshold=f"< {tf_floor:.0%} is farming while team fights",
        ))

    # --- Ward rules (role-specific) ---
    if ward_rule == "flag_if_laning_phase" and metrics.ward_purchases >= config.WARD_PURCHASE_LIMIT:
        errors.append(DetectedError(
            category="Carry buying wards",
            description="Carry purchased wards multiple times instead of farm items",
            severity="medium",
            metric_value=f"{metrics.ward_purchases} ward purchases",
            threshold=f">= {config.WARD_PURCHASE_LIMIT} ward purchases is filling a support role",
        ))
    elif ward_rule == "require_minimum" and metrics.ward_placements is not None and metrics.ward_placements < 8:
        errors.append(DetectedError(
            category="Low ward output",
            description="Support placed fewer wards than expected",
            severity="high",
            metric_value=f"{metrics.ward_placements} wards placed",
            threshold="< 8 wards placed is underperforming for support",
        ))

    # ===================================================================
    # v1 ABSOLUTE RULES (only when no enrichment provided — backward compat)
    # ===================================================================
    if enrichment is None:
        # Rule 1: Poor laning CS
        if metrics.lh_at_10 < config.LH_AT_10_MIN:
            errors.append(DetectedError(
                category="Poor laning CS",
                description="Last hits at 10 min are below the minimum threshold",
                severity="high",
                metric_value=f"{metrics.lh_at_10} LH at 10:00",
                threshold=f"< {config.LH_AT_10_MIN} is poor laning",
            ))

        # Rule 4: Slow core item
        if metrics.first_core_item_minute is not None and metrics.first_core_item_minute > config.SLOW_CORE_ITEM_MINUTES:
            errors.append(DetectedError(
                category="Slow core item",
                description=f"First core item purchased after {config.SLOW_CORE_ITEM_MINUTES:.0f} minutes",
                severity="high",
                metric_value=f"{metrics.first_core_item_name} at {metrics.first_core_item_minute:.1f} min",
                threshold=f"> {config.SLOW_CORE_ITEM_MINUTES:.0f} min is slow farm",
            ))

        # Rule 5: Net worth deficit at 10
        delta_10 = metrics.enemy_carry_net_worth_at_10 - metrics.net_worth_at_10
        if delta_10 > config.NW_DEFICIT_AT_10:
            errors.append(DetectedError(
                category="Net worth deficit at 10",
                description="Enemy carry has a significant gold lead at 10 minutes",
                severity="high",
                metric_value=f"{delta_10:+d}g deficit at 10:00",
                threshold=f"> {config.NW_DEFICIT_AT_10}g deficit is a severe laning loss",
            ))

        # Rule 6: Net worth deficit at 20
        delta_20 = metrics.enemy_carry_net_worth_at_20 - metrics.net_worth_at_20
        if delta_20 > config.NW_DEFICIT_AT_20:
            errors.append(DetectedError(
                category="Net worth deficit at 20",
                description="Enemy carry is more than one major item ahead at 20 minutes",
                severity="critical",
                metric_value=f"{delta_20:+d}g deficit at 20:00",
                threshold=f"> {config.NW_DEFICIT_AT_20}g deficit is generally unrecoverable",
            ))

        # Rule 7: Passive laning
        if metrics.laning_heatmap_own_half_pct > config.PASSIVE_LANING_OWN_HALF_PCT:
            errors.append(DetectedError(
                category="Passive laning",
                description="Spent most of the laning phase on own side of the map",
                severity="medium",
                metric_value=f"{metrics.laning_heatmap_own_half_pct:.0%} of laning in own half",
                threshold=f"> {config.PASSIVE_LANING_OWN_HALF_PCT:.0%} own-half positioning is passive",
            ))

    # ===================================================================
    # v2 PERCENTILE RULES (when enrichment provided, skip for turbo)
    # ===================================================================
    if enrichment is not None and role_profile is not None and not metrics.turbo:
        observed = role_profile.observed_metrics
        benchmarks = enrichment.benchmarks

        for obs_metric in observed:
            bench_key = _METRIC_TO_BENCH.get(obs_metric)
            if bench_key is None:
                continue  # no benchmark available for this metric
            bench = _find_benchmark(benchmarks, bench_key)
            if bench is None:
                continue  # benchmarks didn't include this metric

            sev = _pct_severity(bench.player_pct)
            if sev is None:
                continue  # above all thresholds — no error

            label = _METRIC_LABELS.get(bench_key, bench_key)
            errors.append(DetectedError(
                category=f"Low {label}",
                description=f"{label} is in the {bench.player_pct:.0%} percentile globally for this hero",
                severity=sev,
                metric_value=f"{bench.player_value:.0f} {label}",
                threshold=f"< {SEVERITY_THRESHOLDS[sev]:.0%} percentile",
                player_pct=bench.player_pct,
                context=f"global median: {bench.bracket_avg:.0f} {label}",
            ))

    return sorted(errors, key=lambda e: _SEVERITY_ORDER[e.severity])[:3]
