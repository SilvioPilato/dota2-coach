"""Carry mistake detection with threshold rules."""
from __future__ import annotations

from dota_coach.models import DetectedError, MatchMetrics

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


def detect_errors(metrics: MatchMetrics) -> list[DetectedError]:
    """
    Apply all 10 threshold rules from PRD Section 4.
    Returns errors sorted by severity (critical > high > medium).
    """
    errors: list[DetectedError] = []

    # Rule 1: Poor laning CS
    if metrics.lh_at_10 < 45:
        errors.append(DetectedError(
            category="Poor laning CS",
            description="Last hits at 10 min are below the minimum threshold",
            severity="high",
            metric_value=f"{metrics.lh_at_10} LH at 10:00",
            threshold="< 45 is poor laning",
        ))

    # Rule 2: Unsafe laning — multiple deaths
    if metrics.deaths_before_10 > 2:
        errors.append(DetectedError(
            category="Unsafe laning",
            description="Died more than twice before 10 minutes",
            severity="critical",
            metric_value=f"{metrics.deaths_before_10} deaths before 10:00",
            threshold="> 2 deaths is unsafe laning",
        ))

    # Rule 3: Single early death (before 5 min)
    if (
        metrics.deaths_before_10 == 1
        and metrics.death_timestamps_laning
        and metrics.death_timestamps_laning[0] < 5.0
    ):
        errors.append(DetectedError(
            category="Single early death",
            description="Died before 5 minutes — likely overextension or missing TP",
            severity="high",
            metric_value=f"Died at {metrics.death_timestamps_laning[0]:.1f} min",
            threshold="Death before 5:00 is addressable",
        ))

    # Rule 4: Slow core item
    if metrics.first_core_item_minute is not None and metrics.first_core_item_minute > 18:
        errors.append(DetectedError(
            category="Slow core item",
            description="First core item purchased after 18 minutes",
            severity="high",
            metric_value=f"{metrics.first_core_item_name} at {metrics.first_core_item_minute:.1f} min",
            threshold="> 18 min is slow farm",
        ))

    # Rule 5: Net worth deficit at 10
    delta_10 = metrics.enemy_carry_net_worth_at_10 - metrics.net_worth_at_10
    if delta_10 > 1000:
        errors.append(DetectedError(
            category="Net worth deficit at 10",
            description="Enemy carry has a significant gold lead at 10 minutes",
            severity="high",
            metric_value=f"{delta_10:+d}g deficit at 10:00",
            threshold="> 1000g deficit is a severe laning loss",
        ))

    # Rule 6: Net worth deficit at 20
    delta_20 = metrics.enemy_carry_net_worth_at_20 - metrics.net_worth_at_20
    if delta_20 > 2500:
        errors.append(DetectedError(
            category="Net worth deficit at 20",
            description="Enemy carry is more than one major item ahead at 20 minutes",
            severity="critical",
            metric_value=f"{delta_20:+d}g deficit at 20:00",
            threshold="> 2500g deficit is generally unrecoverable",
        ))

    # Rule 7: Passive laning
    if metrics.laning_heatmap_own_half_pct > 0.70:
        errors.append(DetectedError(
            category="Passive laning",
            description="Spent most of the laning phase on own side of the map",
            severity="medium",
            metric_value=f"{metrics.laning_heatmap_own_half_pct:.0%} of laning in own half",
            threshold="> 70% own-half positioning is passive",
        ))

    # Rule 8: Carry buying wards
    if metrics.ward_purchases >= 2:
        errors.append(DetectedError(
            category="Carry buying wards",
            description="Carry purchased wards multiple times instead of farm items",
            severity="medium",
            metric_value=f"{metrics.ward_purchases} ward purchases",
            threshold=">= 2 ward purchases is filling a support role",
        ))

    # Rule 9: Farming during fights
    if metrics.teamfight_participation_rate is not None and metrics.teamfight_participation_rate < 0.40:
        errors.append(DetectedError(
            category="Farming during fights",
            description="Participated in fewer than 40% of teamfights",
            severity="medium",
            metric_value=f"{metrics.teamfight_participation_rate:.0%} teamfight participation",
            threshold="< 40% is farming while team fights",
        ))

    return sorted(errors, key=lambda e: _SEVERITY_ORDER[e.severity])[:3]
