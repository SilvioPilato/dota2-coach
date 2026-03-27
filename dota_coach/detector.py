"""Carry mistake detection with threshold rules (v2: percentile-based, role-aware)."""
from __future__ import annotations

from typing import Optional

from dota_coach import config
from dota_coach.models import (
    DeathCause,
    DetectedError,
    EnrichmentContext,
    HeroBenchmark,
    MatchMetrics,
    RoleProfile,
)


CORE_ITEMS: frozenset[str] = frozenset({
    # Farming / tempo items
    "item_bfury",           # Battle Fury (parser name — NOT item_battle_fury)
    "item_hand_of_midas",
    "item_radiance",
    "item_maelstrom",
    "item_mjollnir",
    "item_echo_sabre",
    # Fighting / right-click items
    "item_desolator",
    "item_basher",
    "item_abyssal_blade",
    "item_monkey_king_bar",
    "item_greater_crit",    # Daedalus
    "item_butterfly",
    "item_silver_edge",
    "item_invis_sword",     # Shadow Blade
    # Survivability
    "item_black_king_bar",
    "item_skadi",
    "item_heart",
    "item_satanic",
    "item_armlet",
    # Mobility
    "item_blink",
    "item_manta",
    "item_sange_and_yasha",
    "item_yasha",
    # Utility / other common firsts
    "item_diffusal_blade",
    "item_disperser",       # Upgraded Diffusal
    "item_ultimate_scepter",
    "item_kaya",
    "item_helm_of_the_dominator",
    "item_helm_of_the_overlord",
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

    # Deward rule (pos 4/5): low deward percentage
    if ward_rule == "require_minimum" and metrics.deward_pct is not None and metrics.deward_pct < 0.30:
        errors.append(DetectedError(
            category="Low deward rate",
            description="Dewarded fewer than 30% of enemy wards — vision control is lacking",
            severity="medium",
            metric_value=f"{metrics.deward_pct:.0%} of enemy wards dewarded",
            threshold="< 30% deward rate is below expectations for a support",
        ))

    # Stun time rule (pos 3/4/5): low total stun application
    if (
        role_profile is not None
        and "stun_time" in role_profile.observed_metrics
        and metrics.stun_time is not None
        and metrics.stun_time < 10.0
    ):
        errors.append(DetectedError(
            category="Low stun time",
            description="Applied very little crowd control — hero stun abilities may be underused",
            severity="medium",
            metric_value=f"{metrics.stun_time:.1f}s total stun applied",
            threshold="< 10s stun time is underutilizing CC for this role",
        ))

    # Rune control rule (pos 2): low rune pickup percentage
    if (
        role_profile is not None
        and "rune_control_pct" in role_profile.observed_metrics
        and metrics.rune_control_pct is not None
        and metrics.rune_control_pct < 0.20
    ):
        errors.append(DetectedError(
            category="Low rune control",
            description="Picked up fewer than 20% of all runes — mid lane rune control is poor",
            severity="medium",
            metric_value=f"{metrics.rune_control_pct:.0%} of runes collected",
            threshold="< 20% rune control is below mid expectations",
        ))

    # Tower damage rule (pos 2): low building damage for mid
    if (
        role_profile is not None
        and "tower_damage" in role_profile.observed_metrics
        and metrics.tower_damage is not None
        and metrics.tower_damage < 1500
    ):
        errors.append(DetectedError(
            category="Low tower damage",
            description="Dealt very little damage to buildings — mid should pressure towers",
            severity="medium",
            metric_value=f"{metrics.tower_damage} building damage",
            threshold="< 1500 tower damage is low for a mid hero",
        ))

    # Initiation rate rule (pos 3): rarely initiating fights
    if (
        role_profile is not None
        and "initiation_rate" in role_profile.observed_metrics
        and metrics.initiation_rate is not None
        and metrics.initiation_rate < 0.30
    ):
        errors.append(DetectedError(
            category="Low initiation rate",
            description="Initiated fewer than 30% of participated teamfights — offlaner should lead engagements",
            severity="medium",
            metric_value=f"{metrics.initiation_rate:.0%} of fights initiated",
            threshold="< 30% initiation rate is passive for an offlaner",
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
        delta_10 = metrics.opponent_net_worth_at_10 - metrics.net_worth_at_10
        if delta_10 > config.NW_DEFICIT_AT_10:
            errors.append(DetectedError(
                category="Net worth deficit at 10",
                description="Same-role opponent has a significant gold lead at 10 minutes",
                severity="high",
                metric_value=f"{delta_10:+d}g deficit at 10:00",
                threshold=f"> {config.NW_DEFICIT_AT_10}g deficit is a severe laning loss",
            ))

        # Rule 6: Net worth deficit at 20
        delta_20 = metrics.opponent_net_worth_at_20 - metrics.net_worth_at_20
        if delta_20 > config.NW_DEFICIT_AT_20:
            errors.append(DetectedError(
                category="Net worth deficit at 20",
                description="Same-role opponent is more than one major item ahead at 20 minutes",
                severity="critical",
                metric_value=f"{delta_20:+d}g deficit at 20:00",
                threshold=f"> {config.NW_DEFICIT_AT_20}g deficit is generally unrecoverable",
            ))

        # T1: Team had NW lead at 20 but lost
        if (
            metrics.result == "loss"
            and metrics.team_net_worth_at_20 > 0
            and metrics.enemy_team_net_worth_at_20 > 0
        ):
            team_delta_20 = metrics.team_net_worth_at_20 - metrics.enemy_team_net_worth_at_20
            if team_delta_20 >= 5000:
                errors.append(DetectedError(
                    category="Wasted team lead",
                    description=f"Your team had a {team_delta_20:,}g team net worth lead at 20 min but lost",
                    severity="high",
                    metric_value=f"Team NW: {metrics.team_net_worth_at_20:,}g vs {metrics.enemy_team_net_worth_at_20:,}g",
                    threshold="≥5000g team lead that wasn't converted",
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

    # ===================================================================
    # ITEM TIMING CHECK (bootstrap-driven, v2 only)
    # ===================================================================
    # Uses Stratz heroItemBootstrap avg_time_minutes as the timing target.
    # Falls back to config.SLOW_CORE_ITEM_MINUTES when bootstrap is empty.
    if enrichment is not None and metrics.first_core_item_minute is not None:
        _TIMING_WINDOW = 4  # minutes of acceptable deviation
        bootstrap = enrichment.hero_item_bootstrap
        first_item_key = (metrics.first_core_item_name or "").removeprefix("item_")

        if bootstrap:
            matched = next(
                (e for e in bootstrap if e.item_name == first_item_key), None
            )
            if matched:
                overshoot = metrics.first_core_item_minute - matched.avg_time_minutes
                if overshoot > _TIMING_WINDOW:
                    sev = "critical" if overshoot > _TIMING_WINDOW * 2 else "high"
                    errors.append(DetectedError(
                        category="Slow item timing",
                        description=(
                            f"{first_item_key.replace('_', ' ').title()} arrived "
                            f"{overshoot:.1f} min later than the bracket average "
                            f"(~{matched.avg_time_minutes:.0f} min, "
                            f"{matched.match_frequency:.0%} of {metrics.hero} games)"
                        ),
                        severity=sev,
                        metric_value=f"Purchased at {metrics.first_core_item_minute:.1f} min",
                        threshold=f"Expected by ~{matched.avg_time_minutes:.0f} min (±{_TIMING_WINDOW})",
                    ))
            else:
                # Non-standard build — note it but don't penalise
                enrichment.build_note = (
                    f"Non-standard first item: {metrics.first_core_item_name} "
                    f"(not in top builds for this hero/bracket)"
                )
        else:
            # Bootstrap unavailable (no Stratz key or fetch failed) — universal fallback
            if metrics.first_core_item_minute > config.SLOW_CORE_ITEM_MINUTES:
                errors.append(DetectedError(
                    category="Slow core item",
                    description=f"First core item purchased after {config.SLOW_CORE_ITEM_MINUTES:.0f} minutes",
                    severity="high",
                    metric_value=f"{metrics.first_core_item_name} at {metrics.first_core_item_minute:.1f} min",
                    threshold=f"> {config.SLOW_CORE_ITEM_MINUTES:.0f} min is slow farm",
                ))

    # ===================================================================
    # DEATH CAUSE RULES (require death_events populated by _classify_death)
    # ===================================================================

    # D1: Repeated gank/rune deaths
    gank_rune_deaths = [d for d in metrics.death_events if d.cause == DeathCause.GANK_RUNE]
    if len(gank_rune_deaths) >= 2:
        n = len(gank_rune_deaths)
        errors.append(DetectedError(
            category="Rune area ganks",
            description=f"Died {n} times near rune spots — predictable rotations exploiting your rune habit",
            severity="high",
            metric_value=f"{n} GANK_RUNE deaths before 10:00",
            threshold=">= 2 rune-area deaths is a pattern",
        ))

    # D2: Repeated overextension deaths
    overextension_deaths = [d for d in metrics.death_events if d.cause == DeathCause.OVEREXTENSION]
    if len(overextension_deaths) >= 2:
        n = len(overextension_deaths)
        errors.append(DetectedError(
            category="Overextension",
            description=f"Died {n} times while overextended into enemy territory",
            severity="high",
            metric_value=f"{n} overextension deaths before 10:00",
            threshold=">= 2 overextension deaths is a positioning pattern",
        ))

    return sorted(errors, key=lambda e: _SEVERITY_ORDER[e.severity])[:3]
