"""Tests for dota_coach.role — role detection and profiles."""
from __future__ import annotations

import pytest

from dota_coach.role import ROLE_LABELS, ROLE_PROFILES, detect_role, get_role_profile


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_match_meta(account_id: int, lane_role: int, last_hits: int = 200, gold_per_min: int = 400) -> dict:
    """Build a minimal match_meta dict for a single player."""
    return {
        "players": [
            {
                "account_id": account_id,
                "lane_role": lane_role,
                "last_hits": last_hits,
                "gold_per_min": gold_per_min,
                "isRadiant": True,
                "player_slot": 0,
            }
        ]
    }


# ---------------------------------------------------------------------------
# detect_role
# ---------------------------------------------------------------------------

class TestDetectRole:
    def test_safe_lane_core_returns_pos1(self):
        meta = _make_match_meta(123, lane_role=1, last_hits=200)
        assert detect_role(meta, 123) == 1

    def test_safe_lane_support_returns_pos5(self):
        # Two players in the safe lane; account 123 has lower GPM → pos 5 (support)
        meta = {
            "players": [
                {"account_id": 123, "lane_role": 1, "gold_per_min": 200, "isRadiant": True, "player_slot": 0},
                {"account_id": 456, "lane_role": 1, "gold_per_min": 500, "isRadiant": True, "player_slot": 1},
            ]
        }
        assert detect_role(meta, 123) == 5

    def test_mid_returns_pos2(self):
        meta = _make_match_meta(123, lane_role=2, last_hits=150)
        assert detect_role(meta, 123) == 2

    def test_off_lane_core_returns_pos3(self):
        meta = _make_match_meta(123, lane_role=3, last_hits=100)
        assert detect_role(meta, 123) == 3

    def test_off_lane_support_returns_pos4(self):
        # Two players in the offlane; account 123 has lower GPM → pos 4 (soft support)
        meta = {
            "players": [
                {"account_id": 123, "lane_role": 3, "gold_per_min": 200, "isRadiant": True, "player_slot": 0},
                {"account_id": 456, "lane_role": 3, "gold_per_min": 400, "isRadiant": True, "player_slot": 1},
            ]
        }
        assert detect_role(meta, 123) == 4

    def test_jungle_returns_pos4(self):
        meta = _make_match_meta(123, lane_role=4, last_hits=80)
        assert detect_role(meta, 123) == 4

    def test_unknown_lane_role_raises(self):
        meta = _make_match_meta(123, lane_role=99, last_hits=100)
        with pytest.raises(ValueError, match="Unknown lane_role"):
            detect_role(meta, 123)

    def test_missing_account_id_raises(self):
        meta = _make_match_meta(123, lane_role=1)
        with pytest.raises(ValueError, match="not found"):
            detect_role(meta, 999)

    def test_missing_lane_role_falls_back_to_support(self):
        # lane_role=None (e.g. Turbo/unprocessed match) → falls back to heuristics
        meta = {"players": [{"account_id": 123, "last_hits": 5, "gold_per_min": 100}]}
        assert detect_role(meta, 123) == 5  # low stats → support fallback


# ---------------------------------------------------------------------------
# get_role_profile
# ---------------------------------------------------------------------------

class TestGetRoleProfile:
    @pytest.mark.parametrize("role", [1, 2, 3, 4, 5])
    def test_valid_roles(self, role):
        profile = get_role_profile(role)
        assert profile.death_limit_before_10 >= 1
        assert len(profile.observed_metrics) > 0

    def test_pos1_ward_rule(self):
        assert get_role_profile(1).ward_rule == "flag_if_laning_phase"

    def test_pos2_ward_rule_none(self):
        assert get_role_profile(2).ward_rule == "none"

    def test_pos5_ward_rule_require_minimum(self):
        assert get_role_profile(5).ward_rule == "require_minimum"

    def test_invalid_role_raises(self):
        with pytest.raises(ValueError, match="Invalid role 6"):
            get_role_profile(6)

    def test_invalid_role_zero_raises(self):
        with pytest.raises(ValueError, match="Invalid role 0"):
            get_role_profile(0)


# ---------------------------------------------------------------------------
# ROLE_PROFILES completeness
# ---------------------------------------------------------------------------

class TestRoleProfiles:
    def test_all_five_roles_defined(self):
        assert set(ROLE_PROFILES.keys()) == {1, 2, 3, 4, 5}

    def test_all_five_labels_defined(self):
        assert set(ROLE_LABELS.keys()) == {1, 2, 3, 4, 5}

    def test_label_values(self):
        assert ROLE_LABELS[1] == "carry"
        assert ROLE_LABELS[5] == "hard support"

    @pytest.mark.parametrize("role", [1, 2, 3, 4, 5])
    def test_tf_participation_limit_between_0_and_1(self, role):
        p = ROLE_PROFILES[role]
        assert 0 < p.tf_participation_limit < 1
