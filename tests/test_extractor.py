"""Unit tests for extractor.py using the mock fixture."""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from dota_coach.extractor import extract_metrics, extract_metrics_from_opendota

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"

# Steam ID → account ID: 76561198000000001 - 76561197960265728 = 39734273
DROW_ACCOUNT_ID = 39734273
NAGA_ACCOUNT_ID = 39734274

MATCH_META = {
    "match_id": 9999999999,
    "duration": 2100,
    "players": [
        {
            "account_id": DROW_ACCOUNT_ID,
            "player_slot": 0,
            "isRadiant": True,
            "lane_role": 1,
            "win": 0,
            "gold_per_min": 342,
            "xp_per_min": 428,
        },
        {
            "account_id": NAGA_ACCOUNT_ID,
            "player_slot": 128,
            "isRadiant": False,
            "lane_role": 1,
            "win": 1,
        },
    ],
}


@pytest.fixture(scope="module")
def records():
    with open(FIXTURES_DIR / "mock_match.json") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def metrics(records):
    return extract_metrics(records, DROW_ACCOUNT_ID, MATCH_META)


# ---------------------------------------------------------------------------
# Basic identification
# ---------------------------------------------------------------------------

def test_match_id(metrics):
    assert metrics.match_id == 9999999999


def test_hero_name(metrics):
    assert metrics.hero == "DrowRanger"


def test_result_is_loss(metrics):
    # gameWinner_ = 3 (Dire) and our player is Radiant → loss
    assert metrics.result == "loss"


def test_duration_minutes(metrics):
    assert metrics.duration_minutes == pytest.approx(35.0)


# ---------------------------------------------------------------------------
# Laning metrics
# ---------------------------------------------------------------------------

def test_lh_at_10(metrics):
    assert metrics.lh_at_10 == 38


def test_denies_at_10(metrics):
    assert metrics.denies_at_10 == 3


def test_deaths_before_10(metrics):
    # Deaths at 230s and 480s are both < 600s; death at 850s is after
    assert metrics.deaths_before_10 == 2


def test_death_timestamps_laning(metrics):
    assert len(metrics.death_timestamps_laning) == 2
    assert metrics.death_timestamps_laning[0] == pytest.approx(230 / 60)
    assert metrics.death_timestamps_laning[1] == pytest.approx(480 / 60)


def test_net_worth_at_10(metrics):
    assert metrics.net_worth_at_10 == 2800


def test_opponent_net_worth_at_10(metrics):
    assert metrics.opponent_net_worth_at_10 == 4200


def test_net_worth_at_20(metrics):
    assert metrics.net_worth_at_20 == 6200


def test_opponent_net_worth_at_20(metrics):
    assert metrics.opponent_net_worth_at_20 == 9500


# ---------------------------------------------------------------------------
# Farming metrics
# ---------------------------------------------------------------------------

def test_gpm_is_derived_from_final_networth(metrics):
    # gpm comes from match_meta gold_per_min (342)
    assert metrics.gpm == 342


def test_xpm_is_derived_from_final_xp(metrics):
    # xpm comes from match_meta xp_per_min (428)
    assert metrics.xpm == 428


def test_first_core_item_is_manta(metrics):
    assert metrics.first_core_item_name == "item_manta"


def test_first_core_item_minute(metrics):
    assert metrics.first_core_item_minute == pytest.approx(1150 / 60)


# ---------------------------------------------------------------------------
# Positioning & habits
# ---------------------------------------------------------------------------

def test_laning_heatmap_own_half_pct(metrics):
    # Both Drow interval records (t=0 and t=600) have x+y < 256 (Radiant own half)
    assert metrics.laning_heatmap_own_half_pct == pytest.approx(1.0)


def test_ward_purchases(metrics):
    # Two item_ward_dispenser purchases at t=200 and t=350
    assert metrics.ward_purchases == 2


# ---------------------------------------------------------------------------
# Fighting metrics
# ---------------------------------------------------------------------------

def test_teamfight_participation_rate(metrics):
    # From the final interval (t=2100): teamfight_participation = 0.35
    assert metrics.teamfight_participation_rate == pytest.approx(0.35)


def test_teamfight_avg_damage_contribution_is_none(metrics):
    # Not available from odota parser in v1
    assert metrics.teamfight_avg_damage_contribution is None


# ---------------------------------------------------------------------------
# Objectives
# ---------------------------------------------------------------------------

def test_first_roshan_minute(metrics):
    assert metrics.first_roshan_minute == pytest.approx(1470 / 60)


def test_first_tower_minute(metrics):
    assert metrics.first_tower_minute == pytest.approx(810 / 60)


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

def test_raises_when_account_id_not_in_match(records):
    bad_meta = {**MATCH_META, "players": [p for p in MATCH_META["players"] if p["account_id"] != DROW_ACCOUNT_ID]}
    with pytest.raises(ValueError, match="not found in match players"):
        extract_metrics(records, DROW_ACCOUNT_ID, bad_meta)


# ---------------------------------------------------------------------------
# No core item purchased
# ---------------------------------------------------------------------------

def test_no_core_item(records):
    # Strip all core item purchases from records
    from dota_coach.detector import CORE_ITEMS
    filtered = [r for r in records if not (
        r.get("type") == "DOTA_COMBATLOG_PURCHASE"
        and r.get("valuename") in CORE_ITEMS
    )]
    m = extract_metrics(filtered, DROW_ACCOUNT_ID, MATCH_META)
    assert m.first_core_item_name is None
    assert m.first_core_item_minute is None


# ---------------------------------------------------------------------------
# No teamfight data in final interval
# ---------------------------------------------------------------------------

def test_teamfight_none(records):
    # Remove teamfight_participation from every interval record
    stripped = [
        {k: v for k, v in r.items() if k != "teamfight_participation"}
        if r.get("type") == "interval" else r
        for r in records
    ]
    m = extract_metrics(stripped, DROW_ACCOUNT_ID, MATCH_META)
    assert m.teamfight_participation_rate is None


# ---------------------------------------------------------------------------
# extract_metrics_from_opendota — lane matchup extraction
# ---------------------------------------------------------------------------

def _make_player(account_id, is_radiant, lane, hero_name, lane_role=1, **extra):
    p = {
        "account_id": account_id,
        "isRadiant": is_radiant,
        "lane": lane,
        "lane_role": lane_role,
        "hero": {"localized_name": hero_name},
        "win": 0,
        "gold_per_min": 300,
        "xp_per_min": 400,
    }
    p.update(extra)
    return p


def _make_opendota_match(our_player, other_players, game_mode=0):
    return {
        "match_id": 1111111111,
        "duration": 1800,
        "game_mode": game_mode,
        "players": [our_player] + other_players,
    }


def test_lane_enemies_safe_lane():
    """Radiant safe (lane=1) player should see Dire offlane (lane=3) as enemies."""
    our_id = 100
    our = _make_player(our_id, True, lane=1, hero_name="Drow Ranger")
    ally_safe = _make_player(101, True, lane=1, hero_name="Crystal Maiden")
    ally_mid = _make_player(102, True, lane=2, hero_name="Storm Spirit")
    enemy_off = _make_player(201, False, lane=3, hero_name="Axe")
    enemy_off2 = _make_player(202, False, lane=3, hero_name="Ancient Apparition")
    enemy_mid = _make_player(203, False, lane=2, hero_name="Puck")

    match = _make_opendota_match(our, [ally_safe, ally_mid, enemy_off, enemy_off2, enemy_mid])
    m = extract_metrics_from_opendota(our_id, match)

    assert set(m.lane_enemies) == {"Axe", "Ancient Apparition"}
    assert m.lane_allies == ["Crystal Maiden"]


def test_lane_enemies_offlane():
    """Radiant offlane (lane=3) player should see Dire safe (lane=1) as enemies."""
    our_id = 100
    our = _make_player(our_id, True, lane=3, hero_name="Tidehunter")
    ally_off = _make_player(101, True, lane=3, hero_name="Earthshaker")
    enemy_safe = _make_player(201, False, lane=1, hero_name="Juggernaut")
    enemy_safe2 = _make_player(202, False, lane=1, hero_name="Io")

    match = _make_opendota_match(our, [ally_off, enemy_safe, enemy_safe2])
    m = extract_metrics_from_opendota(our_id, match)

    assert set(m.lane_enemies) == {"Juggernaut", "Io"}
    assert m.lane_allies == ["Earthshaker"]


def test_lane_enemies_midlane():
    """Mid (lane=2) player should see enemy mid as opponent."""
    our_id = 100
    our = _make_player(our_id, True, lane=2, hero_name="Invoker")
    enemy_mid = _make_player(201, False, lane=2, hero_name="Sniper")
    enemy_safe = _make_player(202, False, lane=1, hero_name="Wraith King")

    match = _make_opendota_match(our, [enemy_mid, enemy_safe])
    m = extract_metrics_from_opendota(our_id, match)

    assert m.lane_enemies == ["Sniper"]
    assert m.lane_allies == []


def test_lane_matchup_no_lane_field():
    """When lane field is absent, lane_enemies and lane_allies default to empty."""
    our_id = 100
    our = {
        "account_id": our_id,
        "isRadiant": True,
        "lane_role": 1,
        "hero": {"localized_name": "Drow Ranger"},
        "win": 0,
        "gold_per_min": 300,
        "xp_per_min": 400,
    }
    enemy = {
        "account_id": 201,
        "isRadiant": False,
        "lane": 3,
        "hero": {"localized_name": "Axe"},
        "lane_role": 1,
        "win": 1,
    }
    match = _make_opendota_match(our, [enemy])
    m = extract_metrics_from_opendota(our_id, match)

    assert m.lane_enemies == []
    assert m.lane_allies == []


def test_lane_matchup_jungle_lane_returns_empty():
    """When our lane is 4 (jungle/roaming), return empty lists."""
    our_id = 100
    our = _make_player(our_id, True, lane=4, hero_name="Enigma")
    enemy = _make_player(201, False, lane=1, hero_name="Juggernaut")

    match = _make_opendota_match(our, [enemy])
    m = extract_metrics_from_opendota(our_id, match)

    assert m.lane_enemies == []
    assert m.lane_allies == []


def test_lane_matchup_hero_id_fallback():
    """When hero localized_name is absent, fall back to hero_id string."""
    our_id = 100
    our = _make_player(our_id, True, lane=1, hero_name="Drow Ranger")
    enemy_no_name = {
        "account_id": 201,
        "isRadiant": False,
        "lane": 3,
        "hero_id": 42,
        "lane_role": 1,
        "win": 1,
    }
    match = _make_opendota_match(our, [enemy_no_name])
    m = extract_metrics_from_opendota(our_id, match)

    assert m.lane_enemies == ["42"]
