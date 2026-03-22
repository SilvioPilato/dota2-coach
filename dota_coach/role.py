"""Role detection and per-role configuration profiles."""
from __future__ import annotations

from dota_coach.models import RoleProfile

ROLE_LABELS: dict[int, str] = {
    1: "carry",
    2: "mid",
    3: "offlaner",
    4: "soft support",
    5: "hard support",
}

ROLE_PROFILES: dict[int, RoleProfile] = {
    1: RoleProfile(
        observed_metrics=[
            "gpm", "lh_at_10", "first_core_minute",
            "net_worth_delta_10", "net_worth_delta_20",
            "laning_heatmap_own_half_pct", "teamfight_participation",
        ],
        death_limit_before_10=2,
        tf_participation_limit=0.40,
        ward_rule="flag_if_laning_phase",
    ),
    2: RoleProfile(
        observed_metrics=[
            "gpm", "lh_at_10", "first_core_minute",
            "rune_control_pct", "tower_damage",
            "teamfight_participation",
        ],
        death_limit_before_10=1,
        tf_participation_limit=0.45,
        ward_rule="none",
    ),
    3: RoleProfile(
        observed_metrics=[
            "gpm", "lh_at_10", "first_core_minute",
            "stacks_created", "stun_time",
            "teamfight_participation", "initiation_rate",
        ],
        death_limit_before_10=3,
        tf_participation_limit=0.50,
        ward_rule="none",
    ),
    4: RoleProfile(
        observed_metrics=[
            "stacks_created", "ward_placements", "deward_pct",
            "stun_time", "hero_healing", "teamfight_participation",
        ],
        death_limit_before_10=3,
        tf_participation_limit=0.55,
        ward_rule="require_minimum",
    ),
    5: RoleProfile(
        observed_metrics=[
            "ward_placements", "deward_pct", "stacks_created",
            "stun_time", "hero_healing", "teamfight_participation",
        ],
        death_limit_before_10=3,
        tf_participation_limit=0.55,
        ward_rule="require_minimum",
    ),
}


def detect_role(match_meta: dict, account_id: int) -> int:
    """Detect the player's role (1-5) from OpenDota match metadata.

    Uses the lane_role field from the player's entry in match_meta['players'].
    lane_role values: 1=safe lane, 2=mid, 3=off lane, 4=jungle.
    Maps to position: safe lane + core → pos 1, mid → pos 2, offlane → pos 3.
    Supports 4/5 map lane_role=4 (jungle) for roaming supports.

    Raises ValueError if account_id not found or lane_role is missing/invalid.
    """
    player = next(
        (p for p in match_meta.get("players", []) if p.get("account_id") == account_id),
        None,
    )
    if player is None:
        raise ValueError(f"account_id {account_id} not found in match players")

    lane_role = player.get("lane_role")
    if lane_role is None:
        raise ValueError(f"lane_role not available for account_id {account_id}")

    # OpenDota lane_role mapping:
    # 1 = safe lane → pos 1 (carry) or pos 5 (hard support) depending on is_roaming
    # 2 = mid       → pos 2
    # 3 = off lane  → pos 3 (offlaner) or pos 4 (soft support)
    # 4 = jungle    → pos 4 (soft support / roamer)
    #
    # Heuristic: use lane_role directly for pos 1-3.
    # For supports, check if player is a core or support via gold_per_min or last_hits.
    # Simplified: lane_role 1 with low GPM → pos 5, lane_role 3 with low GPM → pos 4.

    is_core = player.get("lane_role") in (1, 2, 3) and (player.get("last_hits", 0) > 50)

    if lane_role == 1:
        return 1 if is_core else 5
    elif lane_role == 2:
        return 2
    elif lane_role == 3:
        return 3 if is_core else 4
    elif lane_role == 4:
        return 4
    else:
        raise ValueError(f"Unknown lane_role {lane_role} for account_id {account_id}")


def get_role_profile(role: int) -> RoleProfile:
    """Return the RoleProfile for a given position (1-5).

    Raises ValueError for invalid role numbers.
    """
    if role not in ROLE_PROFILES:
        raise ValueError(f"Invalid role {role}. Must be 1-5.")
    return ROLE_PROFILES[role]
