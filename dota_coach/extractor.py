"""Maps odota parser NDJSON event records to MatchMetrics."""
from __future__ import annotations

import json
import re
from typing import Any

from dota_coach.models import MatchMetrics

# Imported here to avoid circular import; detector defines CORE_ITEMS
# We do a lazy import inside the function so extractor can be imported independently.

WARD_ITEMS = {"item_ward_dispenser", "item_ward_sentry"}

# Dota 2 map geometry (normalized 0-256 coordinate space from odota parser).
# Radiant own half: x + y < 256  (bottom-left)
# Dire own half:   x + y > 256  (top-right)
_MAP_DIAGONAL = 256.0


def _npc_name_from_unit(unit: str) -> str:
    """Convert 'CDOTA_Unit_Hero_DrowRanger' -> 'npc_dota_hero_drow_ranger'."""
    suffix = unit.replace("CDOTA_Unit_Hero_", "")
    # Insert underscore before uppercase letters (CamelCase -> snake_case)
    snake = re.sub(r"(?<!^)(?=[A-Z])", "_", suffix).lower()
    return f"npc_dota_hero_{snake}"


def _parse_epilogue(epilogue_key: str) -> dict:
    try:
        return json.loads(epilogue_key)
    except Exception:
        return {}


def extract_metrics(
    records: list[dict],
    our_account_id: int,
    match_meta: dict,
) -> MatchMetrics:
    """
    Maps odota parser NDJSON records to MatchMetrics.

    Args:
        records:        list of event dicts from parser.parse_replay()
        our_account_id: 32-bit OpenDota account ID of the player being analyzed
        match_meta:     dict from opendota.get_match() — provides player_slot, duration, etc.
    """
    from dota_coach.detector import CORE_ITEMS

    # --- Step 1: find our player's parser slot ---
    # OpenDota match_meta players[] have 'account_id' and 'player_slot' (0-4 radiant, 128-132 dire)
    our_meta = next(
        (p for p in match_meta["players"] if p.get("account_id") == our_account_id),
        None,
    )
    if our_meta is None:
        raise ValueError(f"account_id {our_account_id} not found in match players")

    game_player_slot = our_meta["player_slot"]

    # parser_slot records: key = parser index (str), value = game player slot
    slot_map: dict[str, int] = {}
    for r in records:
        if r.get("type") == "player_slot":
            slot_map[r["key"]] = r["value"]

    our_parser_slot = next(
        (int(k) for k, v in slot_map.items() if v == game_player_slot),
        None,
    )
    if our_parser_slot is None:
        raise ValueError(f"Could not map game_player_slot {game_player_slot} to parser slot")

    # Determine team: slots 0-4 = Radiant, 5-9 = Dire
    our_team_radiant = our_parser_slot < 5

    # --- Step 2: build unit name → parser slot map from interval records ---
    unit_to_slot: dict[str, int] = {}
    for r in records:
        if r.get("type") == "interval" and "unit" in r and "slot" in r:
            unit_to_slot[r["unit"]] = r["slot"]

    our_unit = next((u for u, s in unit_to_slot.items() if s == our_parser_slot), None)
    if our_unit is None:
        raise ValueError("Could not determine our hero unit name from interval records")

    our_npc_name = _npc_name_from_unit(our_unit)
    our_hero_name = our_unit.replace("CDOTA_Unit_Hero_", "")

    # Identify enemy carry parser slot: opposing team, smallest slot number (proxy for pos 1)
    # Uses OpenDota lane_role from match_meta for accuracy
    enemy_meta = next(
        (
            p for p in match_meta["players"]
            if p.get("account_id") != our_account_id
            and p.get("isRadiant") != our_meta.get("isRadiant")
            and p.get("lane_role") == 1
        ),
        None,
    )
    enemy_parser_slot: int | None = None
    if enemy_meta is not None:
        enemy_game_slot = enemy_meta["player_slot"]
        enemy_parser_slot = next(
            (int(k) for k, v in slot_map.items() if v == enemy_game_slot),
            None,
        )

    # --- Step 3: index interval records by (slot, time) ---
    def get_interval(slot: int, time: int) -> dict | None:
        """Return the interval record for slot closest to the given time."""
        candidates = [
            r for r in records
            if r.get("type") == "interval" and r.get("slot") == slot and r.get("time", -9999) <= time
        ]
        return max(candidates, key=lambda r: r["time"]) if candidates else None

    iv_our_10 = get_interval(our_parser_slot, 600)
    iv_our_20 = get_interval(our_parser_slot, 1200)
    iv_our_final = get_interval(our_parser_slot, 99999)

    iv_enemy_10 = get_interval(enemy_parser_slot, 600) if enemy_parser_slot is not None else None
    iv_enemy_20 = get_interval(enemy_parser_slot, 1200) if enemy_parser_slot is not None else None

    lh_at_10 = iv_our_10["lh"] if iv_our_10 else 0
    denies_at_10 = iv_our_10["denies"] if iv_our_10 else 0
    net_worth_at_10 = iv_our_10["networth"] if iv_our_10 else 0
    net_worth_at_20 = iv_our_20["networth"] if iv_our_20 else 0
    enemy_nw_at_10 = iv_enemy_10["networth"] if iv_enemy_10 else 0
    enemy_nw_at_20 = iv_enemy_20["networth"] if iv_enemy_20 else 0

    # --- Step 4: duration + GPM/XPM ---
    epilogue_rec = next((r for r in records if r.get("type") == "epilogue"), None)
    if epilogue_rec:
        epi = _parse_epilogue(epilogue_rec.get("key", "{}"))
        duration_seconds = epi.get("playbackTime_", match_meta.get("duration", 0))
        game_winner = epi.get("gameInfo_", {}).get("dota_", {}).get("gameWinner_")
    else:
        duration_seconds = match_meta.get("duration", 0)
        game_winner = None

    duration_minutes = duration_seconds / 60.0

    final_nw = iv_our_final["networth"] if iv_our_final else 0
    final_xp = iv_our_final["xp"] if iv_our_final else 0
    gpm = int(final_nw / duration_minutes) if duration_minutes > 0 else 0
    xpm = int(final_xp / duration_minutes) if duration_minutes > 0 else 0

    # --- Step 5: result ---
    if game_winner is not None:
        # gameWinner_: 2 = Radiant wins, 3 = Dire wins
        radiant_won = game_winner == 2
        result = "win" if (radiant_won == our_team_radiant) else "loss"
    else:
        result = "win" if our_meta.get("win") else "loss"

    # --- Step 6: deaths ---
    hero_deaths = [
        r for r in records
        if r.get("type") == "DOTA_COMBATLOG_DEATH"
        and r.get("targethero") is True
        and r.get("targetillusion") is False
        and r.get("targetname") == our_npc_name
    ]
    deaths_before_10 = [d for d in hero_deaths if d["time"] < 600]
    death_timestamps_laning = sorted(d["time"] / 60.0 for d in deaths_before_10)

    # --- Step 7: purchases ---
    our_purchases = [
        r for r in records
        if r.get("type") == "DOTA_COMBATLOG_PURCHASE"
        and r.get("targetname") == our_npc_name
        and r.get("time", -1) > 0
    ]

    ward_purchases = sum(1 for p in our_purchases if p.get("valuename") in WARD_ITEMS)

    core_purchases = [
        p for p in our_purchases if p.get("valuename") in CORE_ITEMS
    ]
    if core_purchases:
        earliest = min(core_purchases, key=lambda p: p["time"])
        first_core_item_minute: float | None = earliest["time"] / 60.0
        first_core_item_name: str | None = earliest["valuename"]
    else:
        first_core_item_minute = None
        first_core_item_name = None

    # --- Step 8: laning heatmap ---
    laning_intervals = [
        r for r in records
        if r.get("type") == "interval"
        and r.get("slot") == our_parser_slot
        and 0 <= r.get("time", -1) <= 600
        and "x" in r and "y" in r
    ]
    if laning_intervals:
        if our_team_radiant:
            own_half = sum(1 for r in laning_intervals if r["x"] + r["y"] < _MAP_DIAGONAL)
        else:
            own_half = sum(1 for r in laning_intervals if r["x"] + r["y"] > _MAP_DIAGONAL)
        laning_heatmap_own_half_pct = own_half / len(laning_intervals)
    else:
        laning_heatmap_own_half_pct = 0.5

    # --- Step 9: teamfight participation (from final interval) ---
    tf_participation: float | None = iv_our_final.get("teamfight_participation") if iv_our_final else None
    # avg damage contribution not available directly from interval — set to None for v1
    tf_avg_damage: float | None = None

    # --- Step 10: objectives ---
    roshan_events = [r for r in records if r.get("type") == "CHAT_MESSAGE_ROSHAN_KILL"]
    first_roshan_minute = min(r["time"] for r in roshan_events) / 60.0 if roshan_events else None

    tower_kills = [
        r for r in records
        if r.get("type") == "DOTA_COMBATLOG_TEAM_BUILDING_KILL"
        and "tower" in r.get("targetname", "")
    ]
    first_tower_minute = min(r["time"] for r in tower_kills) / 60.0 if tower_kills else None

    return MatchMetrics(
        match_id=match_meta["match_id"],
        hero=our_hero_name,
        duration_minutes=duration_minutes,
        result=result,
        lh_at_10=lh_at_10,
        denies_at_10=denies_at_10,
        deaths_before_10=len(deaths_before_10),
        death_timestamps_laning=death_timestamps_laning,
        net_worth_at_10=net_worth_at_10,
        enemy_carry_net_worth_at_10=enemy_nw_at_10,
        net_worth_at_20=net_worth_at_20,
        enemy_carry_net_worth_at_20=enemy_nw_at_20,
        gpm=gpm,
        xpm=xpm,
        first_core_item_minute=first_core_item_minute,
        first_core_item_name=first_core_item_name,
        laning_heatmap_own_half_pct=laning_heatmap_own_half_pct,
        ward_purchases=ward_purchases,
        teamfight_participation_rate=tf_participation,
        teamfight_avg_damage_contribution=tf_avg_damage,
        first_roshan_minute=first_roshan_minute,
        first_tower_minute=first_tower_minute,
    )
