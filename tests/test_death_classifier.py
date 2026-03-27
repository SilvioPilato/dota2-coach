"""Unit tests for _classify_death() covering all 6 DeathCause values plus edge cases."""
from __future__ import annotations

import math

import pytest

from dota_coach.extractor import (
    DIVE_RADIUS,
    DIRE_T1_MID,
    RADIANT_T1_MID,
    _RUNE_RADIUS,
    _RUNE_SPOTS,
    _classify_death,
)
from dota_coach.models import DeathCause


# ---------------------------------------------------------------------------
# Helper constructors
# ---------------------------------------------------------------------------

def _death(time: int = 300, attacker: str = "npc_dota_hero_zuus") -> dict:
    return {
        "time": time,
        "type": "DOTA_COMBATLOG_DEATH",
        "attackername": attacker,
        "targethero": True,
        "targetillusion": False,
    }


def _interval(slot: int, time: int, x: float, y: float) -> dict:
    return {"type": "interval", "slot": slot, "time": time, "x": x, "y": y}


def _tower_kill(time: int, targetname: str = "npc_dota_goodguys_tower1_mid") -> dict:
    return {
        "type": "DOTA_COMBATLOG_TEAM_BUILDING_KILL",
        "time": time,
        "targetname": targetname,
    }


def _tp_purchase(time: int, hero_npc: str = "npc_dota_hero_sandking") -> dict:
    return {
        "type": "DOTA_COMBATLOG_PURCHASE",
        "time": time,
        "valuename": "item_tpscroll",
        "targetname": hero_npc,
    }


# Shared constants for tests
OUR_SLOT = 1
OUR_NPC = "npc_dota_hero_sandking"

# ---------------------------------------------------------------------------
# 1. TEAMFIGHT — death within a fight window where our hero dealt damage
# ---------------------------------------------------------------------------

def test_teamfight_basic():
    """Death at t=300 inside fight [280,320] where our slot dealt damage."""
    death = _death(time=300)
    teamfights = [
        {"start": 280, "end": 320, "players": [{"damage": 0}, {"damage": 50}]}
    ]
    records = [_interval(OUR_SLOT, 299, 100, 100)]
    cause, detail = _classify_death(death, OUR_SLOT, records, teamfights, OUR_NPC, True)
    assert cause == DeathCause.TEAMFIGHT
    assert "teamfight" in detail


def test_teamfight_death_at_fight_boundary_start():
    """Death exactly at fight start time is included (start <= death_time)."""
    death = _death(time=280)
    teamfights = [
        {"start": 280, "end": 320, "players": [{"damage": 0}, {"damage": 10}]}
    ]
    records = [_interval(OUR_SLOT, 279, 100, 100)]
    cause, detail = _classify_death(death, OUR_SLOT, records, teamfights, OUR_NPC, True)
    assert cause == DeathCause.TEAMFIGHT


def test_teamfight_death_at_fight_boundary_end():
    """Death exactly at fight end time is included (death_time <= end)."""
    death = _death(time=320)
    teamfights = [
        {"start": 280, "end": 320, "players": [{"damage": 0}, {"damage": 10}]}
    ]
    records = [_interval(OUR_SLOT, 319, 100, 100)]
    cause, detail = _classify_death(death, OUR_SLOT, records, teamfights, OUR_NPC, True)
    assert cause == DeathCause.TEAMFIGHT


def test_teamfight_no_damage_not_classified():
    """Fight window overlaps death but our slot dealt 0 damage — not TEAMFIGHT."""
    death = _death(time=300)
    teamfights = [
        {"start": 280, "end": 320, "players": [{"damage": 0}, {"damage": 0}]}
    ]
    # Position outside any other classification zone
    records = [_interval(OUR_SLOT, 299, 100, 100)]
    cause, detail = _classify_death(death, OUR_SLOT, records, teamfights, OUR_NPC, True)
    assert cause != DeathCause.TEAMFIGHT


def test_teamfight_slot_out_of_range_not_classified():
    """Fight players list too short for our_parser_slot — not TEAMFIGHT."""
    death = _death(time=300)
    teamfights = [{"start": 280, "end": 320, "players": [{"damage": 50}]}]
    records = [_interval(OUR_SLOT, 299, 100, 100)]
    cause, detail = _classify_death(death, OUR_SLOT, records, teamfights, OUR_NPC, True)
    assert cause != DeathCause.TEAMFIGHT


# ---------------------------------------------------------------------------
# 2. DIVE — died within DIVE_RADIUS of an enemy tower
# ---------------------------------------------------------------------------

def test_dive_radiant_player_near_dire_tower():
    """Radiant player at (143, 139) is within DIVE_RADIUS=10 of DIRE_T1_MID (141, 138)."""
    dist = math.sqrt((143 - 141) ** 2 + (139 - 138) ** 2)
    assert dist <= DIVE_RADIUS, f"test setup error: dist={dist} > DIVE_RADIUS={DIVE_RADIUS}"

    death = _death(time=300)
    records = [_interval(OUR_SLOT, 299, 143, 139)]
    cause, detail = _classify_death(death, OUR_SLOT, records, [], OUR_NPC, True)
    assert cause == DeathCause.DIVE
    assert "DIRE_T1_MID" in detail


def test_dive_dire_player_near_radiant_tower():
    """Dire player (slot 5) at (116, 119) is near RADIANT_T1_MID (115, 118)."""
    dist = math.sqrt((116 - 115) ** 2 + (119 - 118) ** 2)
    assert dist <= DIVE_RADIUS

    death = _death(time=300)
    records = [_interval(5, 299, 116, 119)]
    cause, detail = _classify_death(death, 5, records, [], OUR_NPC, False)
    assert cause == DeathCause.DIVE
    assert "RADIANT_T1_MID" in detail


def test_dive_outside_radius_not_classified():
    """Player far from any tower is not classified as DIVE."""
    death = _death(time=300)
    records = [_interval(OUR_SLOT, 299, 100, 100)]
    cause, _ = _classify_death(death, OUR_SLOT, records, [], OUR_NPC, True)
    assert cause != DeathCause.DIVE


def test_dive_exactly_at_radius_boundary():
    """Player exactly DIVE_RADIUS away from tower qualifies as DIVE (dist <= radius)."""
    tx, ty = DIRE_T1_MID  # (141, 138)
    # Place player exactly DIVE_RADIUS units to the right
    px = tx + DIVE_RADIUS
    py = ty
    dist = math.sqrt((px - tx) ** 2 + (py - ty) ** 2)
    assert dist == DIVE_RADIUS

    death = _death(time=300)
    records = [_interval(OUR_SLOT, 299, px, py)]
    cause, _ = _classify_death(death, OUR_SLOT, records, [], OUR_NPC, True)
    assert cause == DeathCause.DIVE


# ---------------------------------------------------------------------------
# 3. GANK_RUNE — died close to a rune spawn location
# ---------------------------------------------------------------------------

def test_gank_rune_near_top_left_bounty():
    """Player at (109, 127) is near _RUNE_SPOTS[0] = (107, 128)."""
    rx, ry = _RUNE_SPOTS[0]  # (107, 128)
    px, py = 109.0, 127.0
    dist = math.sqrt((px - rx) ** 2 + (py - ry) ** 2)
    assert dist <= _RUNE_RADIUS

    death = _death(time=300)
    records = [_interval(OUR_SLOT, 299, px, py)]
    cause, detail = _classify_death(death, OUR_SLOT, records, [], OUR_NPC, True)
    assert cause == DeathCause.GANK_RUNE
    assert "rune" in detail.lower()


def test_gank_rune_each_spot():
    """Each of the 4 rune spots triggers GANK_RUNE when player is at the exact spot."""
    for rx, ry in _RUNE_SPOTS:
        death = _death(time=300)
        records = [_interval(OUR_SLOT, 299, rx, ry)]
        cause, _ = _classify_death(death, OUR_SLOT, records, [], OUR_NPC, True)
        assert cause == DeathCause.GANK_RUNE, f"Expected GANK_RUNE for rune spot ({rx}, {ry})"


def test_gank_rune_outside_radius_not_classified():
    """Player well outside rune radius is not GANK_RUNE."""
    death = _death(time=300)
    records = [_interval(OUR_SLOT, 299, 100.0, 100.0)]
    cause, _ = _classify_death(death, OUR_SLOT, records, [], OUR_NPC, True)
    assert cause != DeathCause.GANK_RUNE


# ---------------------------------------------------------------------------
# 4. NO_TP_RESPONSE — a tower fell in [death_time-90, death_time], no TP bought
# ---------------------------------------------------------------------------

def test_no_tp_response_tower_fell_no_tp():
    """Tower killed at t=250, death at t=300, no TP purchased → NO_TP_RESPONSE."""
    death = _death(time=300)
    records = [
        _interval(OUR_SLOT, 299, 100, 100),
        _tower_kill(time=250),
    ]
    cause, detail = _classify_death(death, OUR_SLOT, records, [], OUR_NPC, True)
    assert cause == DeathCause.NO_TP_RESPONSE
    assert "tower" in detail.lower() or "TP" in detail or "tp" in detail.lower()


def test_no_tp_response_tower_at_window_edge():
    """Tower killed exactly at death_time - 90 = 210 is included in window."""
    death = _death(time=300)
    records = [
        _interval(OUR_SLOT, 299, 100, 100),
        _tower_kill(time=210),
    ]
    cause, _ = _classify_death(death, OUR_SLOT, records, [], OUR_NPC, True)
    assert cause == DeathCause.NO_TP_RESPONSE


def test_no_tp_response_tower_outside_window_not_classified():
    """Tower killed at t=200, death at t=300 (window is [210,300]) — not in window."""
    death = _death(time=300)
    records = [
        _interval(OUR_SLOT, 299, 100, 100),
        _tower_kill(time=200),
    ]
    cause, _ = _classify_death(death, OUR_SLOT, records, [], OUR_NPC, True)
    assert cause != DeathCause.NO_TP_RESPONSE


def test_no_tp_response_tp_purchased_in_window_skips():
    """TP purchased in window → NO_TP_RESPONSE is skipped; OVEREXTENSION or lower applies."""
    death = _death(time=300)
    # Position that triggers OVEREXTENSION for a Radiant player:
    # Radiant enemy half = x+y > 256. Use (160, 110) → sum=270 > 256.
    records = [
        _interval(OUR_SLOT, 299, 160, 110),
        _tower_kill(time=250),
        _tp_purchase(time=260, hero_npc=OUR_NPC),
    ]
    cause, _ = _classify_death(death, OUR_SLOT, records, [], OUR_NPC, True)
    assert cause != DeathCause.NO_TP_RESPONSE


def test_no_tp_response_tp_for_different_hero_not_counted():
    """TP purchased by a different hero does not satisfy the TP check."""
    death = _death(time=300)
    records = [
        _interval(OUR_SLOT, 299, 100, 100),
        _tower_kill(time=250),
        _tp_purchase(time=260, hero_npc="npc_dota_hero_zuus"),  # wrong hero
    ]
    cause, _ = _classify_death(death, OUR_SLOT, records, [], OUR_NPC, True)
    assert cause == DeathCause.NO_TP_RESPONSE


# ---------------------------------------------------------------------------
# 5. OVEREXTENSION — died in enemy half of the map
#
# Radiant own half: x+y < 256 (bottom-left). Enemy half: x+y > 256.
# Dire own half:   x+y > 256 (top-right).   Enemy half: x+y < 256.
# ---------------------------------------------------------------------------

def test_overextension_radiant_player_in_dire_half():
    """Radiant player at (160, 110): x+y=270 > 256 → in Dire half → OVEREXTENSION."""
    death = _death(time=300)
    records = [_interval(OUR_SLOT, 299, 160, 110)]
    cause, detail = _classify_death(death, OUR_SLOT, records, [], OUR_NPC, True)
    assert cause == DeathCause.OVEREXTENSION
    assert detail != ""


def test_overextension_radiant_player_in_own_half_not_flagged():
    """Radiant player at (100, 100): x+y=200 < 256 → in Radiant half → NOT overextension."""
    death = _death(time=300)
    records = [_interval(OUR_SLOT, 299, 100, 100)]
    cause, _ = _classify_death(death, OUR_SLOT, records, [], OUR_NPC, True)
    assert cause != DeathCause.OVEREXTENSION


def test_overextension_dire_player_in_radiant_half():
    """Dire player (slot 5) at (90, 90): x+y=180 < 256 → in Radiant half, far from towers → OVEREXTENSION."""
    death = _death(time=300)
    records = [_interval(5, 299, 90, 90)]  # x+y=180, far from all Radiant towers
    cause, detail = _classify_death(death, 5, records, [], OUR_NPC, False)
    assert cause == DeathCause.OVEREXTENSION


def test_overextension_dire_player_in_own_half_not_flagged():
    """Dire player at (160, 110): x+y=270 > 256 → in Dire half → NOT overextension."""
    death = _death(time=300)
    records = [_interval(5, 299, 160, 110)]
    cause, _ = _classify_death(death, 5, records, [], OUR_NPC, False)
    assert cause != DeathCause.OVEREXTENSION


# ---------------------------------------------------------------------------
# 6. UNKNOWN — none of the conditions match
# ---------------------------------------------------------------------------

def test_unknown_no_conditions_match():
    """Player at safe neutral position with no teamfights, towers, or runes nearby."""
    death = _death(time=300)
    # Position (128, 128): diagonal = 256, not < 256 and not > 256, so neither half
    # Not near any tower or rune
    records = [_interval(OUR_SLOT, 299, 128, 128)]
    cause, detail = _classify_death(death, OUR_SLOT, records, [], OUR_NPC, True)
    assert cause == DeathCause.UNKNOWN
    assert detail == ""


# ---------------------------------------------------------------------------
# 7. Priority: TEAMFIGHT beats DIVE
# ---------------------------------------------------------------------------

def test_teamfight_priority_over_dive():
    """Death meets both TEAMFIGHT and DIVE conditions → TEAMFIGHT wins (higher priority)."""
    # Position near DIRE_T1_MID (141, 138) for a Radiant player → would be DIVE
    tx, ty = DIRE_T1_MID  # (141, 138)
    px, py = tx + 2, ty  # well within DIVE_RADIUS

    death = _death(time=300)
    records = [_interval(OUR_SLOT, 299, px, py)]
    teamfights = [
        {"start": 280, "end": 320, "players": [{"damage": 0}, {"damage": 100}]}
    ]
    cause, detail = _classify_death(death, OUR_SLOT, records, teamfights, OUR_NPC, True)
    assert cause == DeathCause.TEAMFIGHT


# ---------------------------------------------------------------------------
# 8. TEAMFIGHT priority over NO_TP_RESPONSE
# ---------------------------------------------------------------------------

def test_teamfight_priority_over_no_tp_response():
    """Death is in a teamfight AND a tower fell with no TP → TEAMFIGHT wins."""
    death = _death(time=300)
    records = [
        _interval(OUR_SLOT, 299, 100, 100),
        _tower_kill(time=250),
    ]
    teamfights = [
        {"start": 280, "end": 320, "players": [{"damage": 0}, {"damage": 100}]}
    ]
    cause, _ = _classify_death(death, OUR_SLOT, records, teamfights, OUR_NPC, True)
    assert cause == DeathCause.TEAMFIGHT


# ---------------------------------------------------------------------------
# 9. No interval record before death — graceful degradation
# ---------------------------------------------------------------------------

def test_no_interval_record_no_pos_dependent_causes():
    """Without an interval record, DIVE/GANK_RUNE/OVEREXTENSION cannot fire."""
    death = _death(time=300)
    # No interval records at all
    records = []
    cause, _ = _classify_death(death, OUR_SLOT, records, [], OUR_NPC, True)
    assert cause in (DeathCause.NO_TP_RESPONSE, DeathCause.UNKNOWN)


def test_no_interval_record_with_tower_kill_gives_no_tp_response():
    """Without position, a recent tower kill still triggers NO_TP_RESPONSE."""
    death = _death(time=300)
    records = [_tower_kill(time=250)]
    cause, _ = _classify_death(death, OUR_SLOT, records, [], OUR_NPC, True)
    assert cause == DeathCause.NO_TP_RESPONSE


def test_no_interval_record_no_tower_gives_unknown():
    """Without position and no recent tower kill, result is UNKNOWN."""
    death = _death(time=300)
    records = []
    cause, _ = _classify_death(death, OUR_SLOT, records, [], OUR_NPC, True)
    assert cause == DeathCause.UNKNOWN


# ---------------------------------------------------------------------------
# 10. Death at t=0 — edge case
# ---------------------------------------------------------------------------

def test_death_at_t0_no_interval():
    """Death at t=0 with no interval records at or before t=0 → UNKNOWN."""
    death = _death(time=0)
    # Only a future interval record exists — should not be used (time > 0 = death_time)
    records = [_interval(OUR_SLOT, 10, 100, 100)]
    cause, _ = _classify_death(death, OUR_SLOT, records, [], OUR_NPC, True)
    assert cause == DeathCause.UNKNOWN


def test_death_at_t0_with_interval_at_t0():
    """Death at t=0 with an interval at t=0 is valid (time <= death_time when both 0)."""
    death = _death(time=0)
    # Position at rune spot to give a concrete non-UNKNOWN result if position is found
    rx, ry = _RUNE_SPOTS[0]
    records = [_interval(OUR_SLOT, 0, rx, ry)]
    cause, _ = _classify_death(death, OUR_SLOT, records, [], OUR_NPC, True)
    assert cause == DeathCause.GANK_RUNE


# ---------------------------------------------------------------------------
# 11. Interval record after death time is ignored
# ---------------------------------------------------------------------------

def test_interval_after_death_time_ignored():
    """An interval record with time > death_time should not be used for position lookup."""
    death = _death(time=300)
    # Only interval is at t=301, which is after death — pos lookup returns None
    records = [_interval(OUR_SLOT, 301, 100, 100)]
    cause, _ = _classify_death(death, OUR_SLOT, records, [], OUR_NPC, True)
    # No position → only NO_TP_RESPONSE or UNKNOWN possible
    assert cause in (DeathCause.NO_TP_RESPONSE, DeathCause.UNKNOWN)


# ---------------------------------------------------------------------------
# 12. NO_TP_RESPONSE: non-tower building kill is excluded
# ---------------------------------------------------------------------------

def test_no_tp_response_barracks_kill_excluded():
    """Only 'tower' in targetname triggers NO_TP_RESPONSE; barracks should not."""
    death = _death(time=300)
    records = [
        _interval(OUR_SLOT, 299, 128, 128),
        {"type": "DOTA_COMBATLOG_TEAM_BUILDING_KILL", "time": 250,
         "targetname": "npc_dota_goodguys_rax_melee_mid"},  # no "tower" in name
    ]
    cause, _ = _classify_death(death, OUR_SLOT, records, [], OUR_NPC, True)
    assert cause != DeathCause.NO_TP_RESPONSE


# ---------------------------------------------------------------------------
# 13. Multiple teamfights — only matching one triggers classification
# ---------------------------------------------------------------------------

def test_multiple_teamfights_only_matching_window_fires():
    """Multiple fights; death only falls in one of them → correct one triggers."""
    death = _death(time=300)
    records = [_interval(OUR_SLOT, 299, 100, 100)]
    teamfights = [
        {"start": 100, "end": 200, "players": [{"damage": 0}, {"damage": 99}]},  # not matching time
        {"start": 280, "end": 320, "players": [{"damage": 0}, {"damage": 50}]},  # matching
        {"start": 400, "end": 500, "players": [{"damage": 0}, {"damage": 99}]},  # not matching time
    ]
    cause, detail = _classify_death(death, OUR_SLOT, records, teamfights, OUR_NPC, True)
    assert cause == DeathCause.TEAMFIGHT


def test_multiple_teamfights_none_match_falls_through():
    """Death not in any fight window → TEAMFIGHT not classified."""
    death = _death(time=300)
    records = [_interval(OUR_SLOT, 299, 100, 100)]
    teamfights = [
        {"start": 100, "end": 200, "players": [{"damage": 0}, {"damage": 99}]},
        {"start": 400, "end": 500, "players": [{"damage": 0}, {"damage": 99}]},
    ]
    cause, _ = _classify_death(death, OUR_SLOT, records, teamfights, OUR_NPC, True)
    assert cause != DeathCause.TEAMFIGHT
