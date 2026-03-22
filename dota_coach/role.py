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

    Uses relative GPM ranking within the same team and lane_role group to
    distinguish pos 1 vs pos 5 (both lane_role=1) and pos 3 vs pos 4 (both
    lane_role=3). This avoids absolute LH/GPM thresholds that break for turbo
    games and long matches.

    lane_role values: 1=safe lane, 2=mid, 3=off lane, 4=jungle.

    Raises ValueError if account_id not found or lane_role is missing/invalid.
    """
    players = match_meta.get("players", [])
    player = next((p for p in players if p.get("account_id") == account_id), None)
    if player is None:
        raise ValueError(f"account_id {account_id} not found in match players")

    lane_role = player.get("lane_role")
    if lane_role is None:
        # Turbo and unprocessed matches lack lane_role — fall back to absolute stats
        lh = player.get("last_hits", 0)
        gpm = player.get("gold_per_min", 0)
        if lh > 100 and gpm > 450:
            return 2  # mid
        elif lh > 60:
            return 1  # carry
        elif gpm > 350:
            return 3  # offlaner
        else:
            return 5  # support fallback

    if lane_role == 2:
        return 2
    if lane_role == 4:
        return 4

    # is_roaming=True means OpenDota explicitly flagged this player as a roamer → pos 4
    if player.get("is_roaming"):
        return 4

    # For lane_role 1 and 3, multiple teammates share the same lane.
    # Rank by GPM within the same team + lane_role group:
    #   lane_role=1: highest GPM → pos 1 (carry), rest → pos 5 (hard support)
    #   lane_role=3: highest GPM → pos 3 (offlaner), rest → pos 4 (soft support)
    our_team_radiant = player.get("isRadiant")
    same_lane_group = [
        p for p in players
        if p.get("isRadiant") == our_team_radiant
        and p.get("lane_role") == lane_role
        and not p.get("is_roaming")
    ]
    same_lane_group.sort(key=lambda p: p.get("gold_per_min", 0), reverse=True)
    is_highest_gpm = same_lane_group[0].get("account_id") == account_id

    if lane_role == 1:
        return 1 if is_highest_gpm else 5
    if lane_role == 3:
        return 3 if is_highest_gpm else 4

    raise ValueError(f"Unknown lane_role {lane_role} for account_id {account_id}")


def get_role_profile(role: int) -> RoleProfile:
    """Return the RoleProfile for a given position (1-5).

    Raises ValueError for invalid role numbers.
    """
    if role not in ROLE_PROFILES:
        raise ValueError(f"Invalid role {role}. Must be 1-5.")
    return ROLE_PROFILES[role]
