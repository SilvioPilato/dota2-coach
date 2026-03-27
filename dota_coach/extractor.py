"""Maps odota parser NDJSON event records to MatchMetrics."""
from __future__ import annotations

import json
import math as _math
import re
from typing import Any

from dota_coach.models import DeathCause, DeathEvent, MatchMetrics

# Imported here to avoid circular import; detector defines CORE_ITEMS
# We do a lazy import inside the function so extractor can be imported independently.

WARD_ITEMS = {"item_ward_dispenser", "item_ward_sentry"}

# Radius (in 0-256 normalised coordinate space) within which a death near an
# enemy tower is classified as a DIVE.
DIVE_RADIUS = 10

# Rune spawn locations in 0-256 normalised coordinate space.
# Bounty runes sit at the river edges; power runes at mid-river.
_RUNE_SPOTS = [
    (107.0, 128.0),  # bounty rune — top-left river
    (149.0, 128.0),  # bounty rune — top-right river
    (128.0, 107.0),  # power rune — bottom river
    (128.0, 149.0),  # power rune — top river
]
_RUNE_RADIUS = 15.0

# 90-second window used for the NO_TP_RESPONSE check.
_TP_WINDOW_SECS = 90

# --- Tower coordinates in 0-256 normalised coordinate space (odota parser) ---
# The odota parser normalises Dota 2 world space to a 0-256 grid:
#   (75, 75)   ≈ Radiant fountain (bottom-left)
#   (180, 180) ≈ Dire fountain    (top-right)
# x increases going right; y increases going up.
# Lane orientation:
#   Bot lane runs along the bottom edge at low y, high x toward Dire.
#   Top lane runs along the left edge at low x, high y toward Dire.
#   Mid lane runs diagonally from bottom-left to top-right.
# Map diagonal x + y = 256 separates Radiant half (x+y < 256) from Dire half (x+y > 256).
#
# Radiant T1/T2 positions derived from fixtures/sample_match.json by locating
# the attacker hero positions (interval records) at the moment each tower was
# killed (DOTA_COMBATLOG_TEAM_BUILDING_KILL).  Dire positions obtained by
# 180-degree rotational symmetry: Dire(x, y) = (256 - Radiant_mirror_x, 256 - Radiant_mirror_y).
#
# Radiant T1 towers
RADIANT_T1_TOP = (80, 146)   # derived: Oracle/PA at (81,148)/(79,145) at kill t=1041
RADIANT_T1_MID = (115, 118)  # derived: Naga Siren at (117,119) at kill t=777
RADIANT_T1_BOT = (167, 79)   # derived: Omniknight/Naga at (166,78)/(168,81) at kill t=887
# Radiant T2 towers
RADIANT_T2_TOP = (78, 123)   # derived: Omniknight/Naga at (77,123)/(79,123) at kill t=1353
RADIANT_T2_MID = (100, 102)  # estimated by mid-lane geometry (no kill in fixture)
RADIANT_T2_BOT = (123, 79)   # derived: Naga Siren at (123,79) at kill t=1067
# Dire T1 towers  (mirror of Radiant T1 across map centre)
DIRE_T1_TOP = (89, 177)      # mirror of RADIANT_T1_BOT
DIRE_T1_MID = (141, 138)     # mirror of RADIANT_T1_MID
DIRE_T1_BOT = (176, 110)     # mirror of RADIANT_T1_TOP
# Dire T2 towers  (mirror of Radiant T2 across map centre)
DIRE_T2_TOP = (133, 177)     # mirror of RADIANT_T2_BOT
DIRE_T2_MID = (156, 154)     # mirror of RADIANT_T2_MID
DIRE_T2_BOT = (178, 133)     # mirror of RADIANT_T2_TOP

# Dota 2 map geometry (normalized 0-256 coordinate space from odota parser).
# Radiant own half: x + y < 256  (bottom-left)
# Dire own half:   x + y > 256  (top-right)
_MAP_DIAGONAL = 256.0


def _classify_death(
    death: dict,
    our_parser_slot: int,
    records: list[dict],
    teamfights: list[dict],
    our_npc_name: str,
    our_team_radiant: bool,
) -> tuple[DeathCause, str]:
    """Classify a single hero death into a DeathCause.

    Args:
        death: The DOTA_COMBATLOG_DEATH record for our hero
        our_parser_slot: Parser slot index for our hero
        records: All parser records (for interval/purchase lookup)
        teamfights: match_meta["teamfights"] list (from OpenDota)
        our_npc_name: e.g. "npc_dota_hero_sand_king"
        our_team_radiant: True if our team is Radiant

    Returns:
        (DeathCause, cause_detail_string)
    """
    death_time: int = death.get("time", 0)
    death_time_min: float = death_time / 60.0

    # ------------------------------------------------------------------
    # 1. TEAMFIGHT — death window overlaps a fight where we dealt damage
    # ------------------------------------------------------------------
    for fight in teamfights:
        start_t = fight.get("start", 0)
        end_t = fight.get("end", start_t)
        if not (start_t <= death_time <= end_t):
            continue
        player_entries = fight.get("players") or []
        if our_parser_slot >= len(player_entries):
            continue
        if (player_entries[our_parser_slot] or {}).get("damage", 0) > 0:
            start_min = start_t / 60.0
            return DeathCause.TEAMFIGHT, f"teamfight at {start_min:.1f}min"

    # ------------------------------------------------------------------
    # Helper: get our position from interval records at death time
    # ------------------------------------------------------------------
    def _position_at(slot: int, time: int) -> tuple[float, float] | None:
        candidates = [
            r for r in records
            if r.get("type") == "interval"
            and r.get("slot") == slot
            and r.get("time", -9999) <= time
            and "x" in r and "y" in r
        ]
        if not candidates:
            return None
        rec = max(candidates, key=lambda r: r["time"])
        return rec["x"], rec["y"]

    pos = _position_at(our_parser_slot, death_time)

    # ------------------------------------------------------------------
    # 2. DIVE — died within DIVE_RADIUS of an enemy tower
    # ------------------------------------------------------------------
    # Enemy towers: RADIANT towers if we are Dire; DIRE towers if we are Radiant
    if our_team_radiant:
        enemy_towers = {
            "DIRE_T1_TOP": DIRE_T1_TOP,
            "DIRE_T1_MID": DIRE_T1_MID,
            "DIRE_T1_BOT": DIRE_T1_BOT,
            "DIRE_T2_TOP": DIRE_T2_TOP,
            "DIRE_T2_MID": DIRE_T2_MID,
            "DIRE_T2_BOT": DIRE_T2_BOT,
        }
    else:
        enemy_towers = {
            "RADIANT_T1_TOP": RADIANT_T1_TOP,
            "RADIANT_T1_MID": RADIANT_T1_MID,
            "RADIANT_T1_BOT": RADIANT_T1_BOT,
            "RADIANT_T2_TOP": RADIANT_T2_TOP,
            "RADIANT_T2_MID": RADIANT_T2_MID,
            "RADIANT_T2_BOT": RADIANT_T2_BOT,
        }

    if pos is not None:
        px, py = pos
        for tower_name, (tx, ty) in enemy_towers.items():
            dist = _math.sqrt((px - tx) ** 2 + (py - ty) ** 2)
            if dist <= DIVE_RADIUS:
                return DeathCause.DIVE, f"died near {tower_name} at {death_time_min:.1f}min"

    # ------------------------------------------------------------------
    # 3. GANK_RUNE — died close to a rune spawn location
    # ------------------------------------------------------------------
    if pos is not None:
        px, py = pos
        for rx, ry in _RUNE_SPOTS:
            dist = _math.sqrt((px - rx) ** 2 + (py - ry) ** 2)
            if dist <= _RUNE_RADIUS:
                return DeathCause.GANK_RUNE, f"near rune location at {death_time_min:.1f}min"

    # ------------------------------------------------------------------
    # 4. NO_TP_RESPONSE — a tower fell in the 90s before death and our
    #    player did not buy a TP scroll in that window
    # ------------------------------------------------------------------
    window_start = death_time - _TP_WINDOW_SECS
    recent_tower_kills = [
        r for r in records
        if r.get("type") == "DOTA_COMBATLOG_TEAM_BUILDING_KILL"
        and "tower" in r.get("targetname", "")
        and window_start <= r.get("time", -1) <= death_time
    ]
    if recent_tower_kills:
        tp_purchased = any(
            r for r in records
            if r.get("type") == "DOTA_COMBATLOG_PURCHASE"
            and r.get("targetname") == our_npc_name
            and r.get("valuename") == "item_tpscroll"
            and window_start <= r.get("time", -1) <= death_time
        )
        if not tp_purchased:
            earliest_tower = min(recent_tower_kills, key=lambda r: r["time"])
            tower_time_min = earliest_tower["time"] / 60.0
            return (
                DeathCause.NO_TP_RESPONSE,
                f"tower fell at {tower_time_min:.1f}min, no TP purchased",
            )

    # ------------------------------------------------------------------
    # 5. OVEREXTENSION — died in enemy territory
    # ------------------------------------------------------------------
    if pos is not None:
        px, py = pos
        diagonal = px + py
        in_enemy_half = (diagonal > _MAP_DIAGONAL) if our_team_radiant else (diagonal < _MAP_DIAGONAL)
        if in_enemy_half:
            return DeathCause.OVEREXTENSION, f"died in enemy territory at {death_time_min:.1f}min"

    # ------------------------------------------------------------------
    # 6. UNKNOWN
    # ------------------------------------------------------------------
    return DeathCause.UNKNOWN, ""


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
    core_items: frozenset | None = None,
) -> MatchMetrics:
    """
    Maps odota parser NDJSON records to MatchMetrics.

    Args:
        records:        list of event dicts from parser.parse_replay()
        our_account_id: 32-bit OpenDota account ID of the player being analyzed
        match_meta:     dict from opendota.get_match() — provides player_slot, duration, etc.
        core_items:     frozenset of parser item names to watch (e.g. ``item_bfury``).
                        Defaults to the static ``detector.CORE_ITEMS`` if not provided.
                        Pass the result of ``enricher.get_core_items()`` for patch-current data.
    """
    from dota_coach.detector import CORE_ITEMS
    _core_items = core_items if core_items is not None else CORE_ITEMS

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

    # Identify same-role opponent: opposing team, same lane_role as our player
    # Uses OpenDota lane_role from match_meta for accuracy
    our_lane_role = our_meta.get("lane_role", 1)
    enemy_meta = next(
        (
            p for p in match_meta["players"]
            if p.get("account_id") != our_account_id
            and p.get("isRadiant") != our_meta.get("isRadiant")
            and p.get("lane_role") == our_lane_role
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

    # Team NW at 20: sum networth across all 5 slots per team
    def get_team_nw(team_slots: range, time: int) -> int:
        total = 0
        for slot in team_slots:
            iv = get_interval(slot, time)
            if iv:
                total += iv.get("networth", 0)
        return total

    radiant_slots = range(0, 5)
    dire_slots = range(5, 10)
    our_team_slots = radiant_slots if our_team_radiant else dire_slots
    enemy_team_slots = dire_slots if our_team_radiant else radiant_slots

    team_nw_at_20 = get_team_nw(our_team_slots, 1200)
    enemy_team_nw_at_20 = get_team_nw(enemy_team_slots, 1200)

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

    # Use OpenDota's authoritative gold_per_min / xp_per_min from match metadata.
    # These are gold *earned* / time and XP / time — not derivable from replay networth
    # (networth = current wealth, not income; diverges from GPM for long games).
    gpm = our_meta.get("gold_per_min", 0)
    xpm = our_meta.get("xp_per_min", 0)

    # --- Step 5: result ---
    if game_winner is not None:
        # gameWinner_: 2 = Radiant wins, 3 = Dire wins
        radiant_won = game_winner == 2
        result = "win" if (radiant_won == our_team_radiant) else "loss"
    else:
        result = "win" if our_meta.get("win") else "loss"

    # Hoist teamfights_meta so it can be used for death classification below.
    teamfights_meta = match_meta.get("teamfights") or []

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

    death_events: list[DeathEvent] = []
    for d in deaths_before_10:
        cause, detail = _classify_death(
            d, our_parser_slot, records, teamfights_meta, our_npc_name, our_team_radiant
        )
        death_events.append(DeathEvent(
            time_minutes=d["time"] / 60.0,
            killer=d.get("attackername", "unknown"),
            cause=cause,
            cause_detail=detail,
        ))

    # --- Step 7: purchases ---
    our_purchases = [
        r for r in records
        if r.get("type") == "DOTA_COMBATLOG_PURCHASE"
        and r.get("targetname") == our_npc_name
        and r.get("time", -1) > 0
    ]

    ward_purchases = sum(1 for p in our_purchases if p.get("valuename") in WARD_ITEMS)

    core_purchases = [
        p for p in our_purchases if p.get("valuename") in _core_items
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

    # --- Step 11: all-role metrics (v2) ---
    # ward_placements: obs_placed + sen_placed from final interval
    ward_placements_val: int | None = None
    if iv_our_final is not None:
        obs_p = iv_our_final.get("obs_placed")
        sen_p = iv_our_final.get("sen_placed")
        if obs_p is not None and sen_p is not None:
            ward_placements_val = obs_p + sen_p

    # stacks_created: from match_meta players[] entry
    stacks_created_val: int | None = our_meta.get("camps_stacked")

    # hero_healing: from match_meta players[] entry
    hero_healing_val: int | None = our_meta.get("hero_healing")

    # stun_time: total stun duration (seconds) applied to enemies, from match_meta
    stun_time_val: float | None = our_meta.get("stuns")

    # rune_control_pct: fraction of all picked runes that our player collected
    # Uses parser "rune" records (type=rune, slot=parser slot of picker)
    all_rune_picks = [r for r in records if r.get("type") == "rune"]
    our_rune_picks = [r for r in all_rune_picks if r.get("slot") == our_parser_slot]
    rune_control_pct_val: float | None = (
        len(our_rune_picks) / len(all_rune_picks) if all_rune_picks else None
    )

    # tower_damage: total damage dealt to buildings, from match_meta
    tower_damage_val: int | None = our_meta.get("tower_damage")

    # initiation_rate: fraction of participated fights where our player dealt the first hero damage
    # Uses match_meta teamfights[] for fight windows + parser damage records for timing
    # (teamfights_meta already collected above for death classification)
    fights_participated = 0
    fights_initiated = 0
    for fight in teamfights_meta:
        start_t = fight.get("start", 0)
        end_t = fight.get("end", start_t)
        player_entries = fight.get("players") or []
        if our_parser_slot >= len(player_entries):
            continue
        # Only count fights where our player dealt damage (active participant)
        if not (player_entries[our_parser_slot] or {}).get("damage", 0):
            continue
        fights_participated += 1
        fight_damage = [
            r for r in records
            if r.get("type") == "DOTA_COMBATLOG_DAMAGE"
            and start_t <= r.get("time", -1) <= end_t
            and r.get("targethero") is True
            and not r.get("targetillusion")
        ]
        if fight_damage:
            fight_damage.sort(key=lambda r: r["time"])
            if fight_damage[0].get("attackername", "") == our_npc_name:
                fights_initiated += 1
    initiation_rate_val: float | None = (
        fights_initiated / fights_participated if fights_participated > 0 else None
    )

    # deward_pct: fraction of enemy wards killed by our player
    # obs/sen records track ward placements; obs_left/sen_left track ward deaths
    # We count enemy ward placements (slot != our_parser_slot) and compare to
    # obs_left/sen_left where attackername matches our hero npc name.
    enemy_ward_count = sum(
        1 for r in records
        if r.get("type") in ("obs", "sen") and r.get("slot") != our_parser_slot
    )
    wards_dewarded = sum(
        1 for r in records
        if r.get("type") in ("obs_left", "sen_left")
        and r.get("attackername") == our_npc_name
    )
    deward_pct_val: float | None = (
        wards_dewarded / enemy_ward_count if enemy_ward_count > 0 else None
    )

    total_last_hits = our_meta.get("last_hits", 0)

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
        net_worth_at_20=net_worth_at_20,
        opponent_net_worth_at_10=enemy_nw_at_10,
        opponent_net_worth_at_20=enemy_nw_at_20,
        team_net_worth_at_20=team_nw_at_20,
        enemy_team_net_worth_at_20=enemy_team_nw_at_20,
        gpm=gpm,
        xpm=xpm,
        total_last_hits=total_last_hits,
        first_core_item_minute=first_core_item_minute,
        first_core_item_name=first_core_item_name,
        laning_heatmap_own_half_pct=laning_heatmap_own_half_pct,
        ward_purchases=ward_purchases,
        teamfight_participation_rate=tf_participation,
        teamfight_avg_damage_contribution=tf_avg_damage,
        first_roshan_minute=first_roshan_minute,
        first_tower_minute=first_tower_minute,
        ward_placements=ward_placements_val,
        stacks_created=stacks_created_val,
        hero_healing=hero_healing_val,
        deward_pct=deward_pct_val,
        stun_time=stun_time_val,
        rune_control_pct=rune_control_pct_val,
        tower_damage=tower_damage_val,
        initiation_rate=initiation_rate_val,
        turbo=match_meta.get("game_mode") == 23,
        death_events=death_events,
    )


def extract_metrics_from_opendota(
    our_account_id: int,
    match_meta: dict,
) -> MatchMetrics:
    """Degraded fallback: build MatchMetrics from OpenDota match_meta only.

    Used when the Valve CDN replay has expired. The following fields are
    unavailable and are set to safe defaults/None:
      - deaths_before_10 (set to 0)
      - death_timestamps_laning (set to [])
      - first_core_item_minute / first_core_item_name (set to None)
      - laning_heatmap_own_half_pct (set to 0.5 — neutral)
      - teamfight_participation_rate derived from interval records (set to None)
    """
    our_meta = next(
        (p for p in match_meta["players"] if p.get("account_id") == our_account_id),
        None,
    )
    if our_meta is None:
        raise ValueError(f"account_id {our_account_id} not found in match players")

    our_unit = our_meta.get("hero_id", "Unknown")
    our_hero_name = our_meta.get("hero", {}).get("localized_name", "") or our_meta.get("hero_id", "Unknown")
    # OpenDota players[] include personaname, hero info may be nested; use a robust fallback
    # Try common field shapes from OpenDota API
    if not our_hero_name or our_hero_name == "Unknown":
        our_hero_name = str(our_unit)

    duration_seconds = match_meta.get("duration", 0)
    duration_minutes = duration_seconds / 60.0

    gpm = our_meta.get("gold_per_min", 0)
    xpm = our_meta.get("xp_per_min", 0)
    total_last_hits = our_meta.get("last_hits", 0)

    # Extract net worth and LH at 10 from time-series arrays
    gold_t: list[int] = our_meta.get("gold_t") or []
    lh_t: list[int] = our_meta.get("lh_t") or []
    net_worth_at_10 = gold_t[10] if len(gold_t) > 10 else 0
    lh_at_10 = lh_t[10] if len(lh_t) > 10 else 0

    # Same-role opponent net worth: find opposing player with matching lane_role
    our_is_radiant = our_meta.get("isRadiant", True)
    our_lane_role = our_meta.get("lane_role", 1)
    enemy_meta = next(
        (
            p for p in match_meta["players"]
            if p.get("account_id") != our_account_id
            and p.get("isRadiant") != our_is_radiant
            and p.get("lane_role") == our_lane_role
        ),
        None,
    )
    enemy_gold_t: list[int] = (enemy_meta or {}).get("gold_t") or []
    enemy_nw_at_10 = enemy_gold_t[10] if len(enemy_gold_t) > 10 else 0
    net_worth_at_20 = gold_t[20] if len(gold_t) > 20 else 0
    enemy_nw_at_20 = enemy_gold_t[20] if len(enemy_gold_t) > 20 else 0

    # Team NW from gold_t arrays
    all_players = match_meta.get("players", [])
    allies = [p for p in all_players if p.get("isRadiant") == our_is_radiant]
    enemies = [p for p in all_players if p.get("isRadiant") != our_is_radiant]

    def _team_nw_at(players: list, minute: int) -> int:
        total = 0
        for p in players:
            gt = p.get("gold_t") or []
            if len(gt) > minute:
                total += gt[minute]
        return total

    team_nw_at_20 = _team_nw_at(allies, 20)
    enemy_team_nw_at_20 = _team_nw_at(enemies, 20)

    result = "win" if our_meta.get("win") else "loss"
    denies_at_10 = 0  # not available per-minute from match_meta

    return MatchMetrics(
        match_id=match_meta["match_id"],
        hero=our_hero_name,
        duration_minutes=duration_minutes,
        result=result,
        lh_at_10=lh_at_10,
        denies_at_10=denies_at_10,
        deaths_before_10=0,          # unavailable in degraded mode
        death_timestamps_laning=[],  # unavailable in degraded mode
        net_worth_at_10=net_worth_at_10,
        net_worth_at_20=net_worth_at_20,
        opponent_net_worth_at_10=enemy_nw_at_10,
        opponent_net_worth_at_20=enemy_nw_at_20,
        team_net_worth_at_20=team_nw_at_20,
        enemy_team_net_worth_at_20=enemy_team_nw_at_20,
        gpm=gpm,
        xpm=xpm,
        total_last_hits=total_last_hits,
        first_core_item_minute=None,          # unavailable in degraded mode
        first_core_item_name=None,            # unavailable in degraded mode
        laning_heatmap_own_half_pct=0.5,     # unavailable — neutral default
        ward_purchases=our_meta.get("purchase_ward_observer", 0) or 0,
        teamfight_participation_rate=None,    # unavailable in degraded mode
        teamfight_avg_damage_contribution=None,
        first_roshan_minute=None,
        first_tower_minute=None,
        ward_placements=(
            (our_meta.get("obs_placed") or 0) + (our_meta.get("sen_placed") or 0) or None
        ),
        stacks_created=our_meta.get("camps_stacked"),
        hero_healing=our_meta.get("hero_healing"),
        stun_time=our_meta.get("stuns"),
        tower_damage=our_meta.get("tower_damage"),
        turbo=match_meta.get("game_mode") == 23,
    )


def build_timeline(records: list[dict], our_npc_name: str, hero_display: str = "") -> str:
    """Build a compact chronological event log for chat system prompt.

    Args:
        records: list of event dicts from parser.parse_replay()
        our_npc_name: npc_dota_hero_* name for our player
        hero_display: human-readable hero name for the header

    Returns:
        Multi-line string of events, or empty string if no events found.
    """
    events: list[tuple[int, str]] = []

    for r in records:
        rtype = r.get("type", "")
        rtime = r.get("time", 0)

        # Player deaths
        if (
            rtype == "DOTA_COMBATLOG_DEATH"
            and r.get("targethero") is True
            and r.get("targetillusion") is False
            and r.get("targetname") == our_npc_name
        ):
            attacker = r.get("attackername", "unknown")
            attacker_short = attacker.replace("npc_dota_hero_", "").replace("_", " ").title()
            events.append((rtime, f"you died to {attacker_short}"))

        # Item purchases (our player)
        if rtype == "DOTA_COMBATLOG_PURCHASE" and r.get("targetname") == our_npc_name and rtime > 0:
            item = r.get("valuename", "unknown").replace("item_", "").replace("_", " ").title()
            events.append((rtime, f"you purchased {item}"))

        # Tower kills
        if rtype == "DOTA_COMBATLOG_TEAM_BUILDING_KILL" and "tower" in r.get("targetname", ""):
            tower_name = r.get("targetname", "tower")
            team = "Radiant" if "goodguys" in tower_name else "Dire"
            events.append((rtime, f"{tower_name} destroyed ({team} side)"))

        # Roshan
        if rtype == "CHAT_MESSAGE_ROSHAN_KILL":
            events.append((rtime, "Roshan killed"))

        # Ward placements (our player)
        if rtype in ("obs", "sen") and r.get("slot") is not None:
            # We don't have our_parser_slot here, so include all ward events
            ward_type = "observer" if rtype == "obs" else "sentry"
            events.append((rtime, f"{ward_type} ward placed"))

    if not events:
        return ""

    # Sort chronologically and format
    events.sort(key=lambda e: e[0])
    lines = []
    header = f"MATCH TIMELINE ({hero_display}):" if hero_display else "MATCH TIMELINE:"
    lines.append(header)
    for t, desc in events:
        minutes = t // 60
        seconds = t % 60
        lines.append(f"{minutes:02d}:{seconds:02d} — {desc}")

    return "\n".join(lines)
