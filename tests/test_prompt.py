"""Tests for prompt.py: token budget and message format (v2: role-aware)."""
from __future__ import annotations

import warnings

import pytest

from dota_coach.models import DetectedError, EnrichmentContext, HeroBenchmark, MatchMetrics
from dota_coach.prompt import build_system_prompt, build_user_message

_TOKEN_BUDGET = 800


def _make_metrics(**overrides) -> MatchMetrics:
    defaults = dict(
        match_id=1,
        hero="DrowRanger",
        duration_minutes=35.0,
        result="loss",
        lh_at_10=38,
        denies_at_10=3,
        deaths_before_10=2,
        death_timestamps_laning=[3.83, 8.0],
        net_worth_at_10=2800,
        enemy_carry_net_worth_at_10=4200,
        net_worth_at_20=6200,
        enemy_carry_net_worth_at_20=9500,
        gpm=342,
        xpm=428,
        first_core_item_minute=19.17,
        first_core_item_name="item_manta",
        laning_heatmap_own_half_pct=1.0,
        ward_purchases=2,
        teamfight_participation_rate=0.35,
        teamfight_avg_damage_contribution=None,
        first_roshan_minute=24.5,
        first_tower_minute=13.5,
    )
    defaults.update(overrides)
    return MatchMetrics(**defaults)


def _sample_errors() -> list[DetectedError]:
    return [
        DetectedError(
            category="Unsafe laning",
            description="Died more than twice before 10 minutes",
            severity="critical",
            metric_value="3 deaths before 10:00",
            threshold="> 2 deaths is unsafe laning",
        ),
        DetectedError(
            category="Net worth deficit at 20",
            description="Enemy carry is more than one major item ahead",
            severity="critical",
            metric_value="+3300g deficit at 20:00",
            threshold="> 2500g deficit is generally unrecoverable",
        ),
        DetectedError(
            category="Slow core item",
            description="First core item purchased after 18 minutes",
            severity="high",
            metric_value="item_manta at 19.2 min",
            threshold="> 18 min is slow farm",
        ),
    ]


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

def test_system_prompt_contains_role():
    s = build_system_prompt()
    assert "carry" in s.lower()
    assert "position 1" in s.lower()


def test_system_prompt_contains_format_instructions():
    s = build_system_prompt()
    assert "MISTAKE 1" in s
    assert "PRIORITY FOCUS" in s


def test_system_prompt_mentions_bracket():
    s = build_system_prompt()
    # v2: bracket references replaced with percentile-based approach
    assert "percentile" in s.lower() or "Dota 2 coach" in s


# ---------------------------------------------------------------------------
# User message — required sections
# ---------------------------------------------------------------------------

def test_user_message_contains_match_header():
    msg = build_user_message(_make_metrics(), [])
    assert "DrowRanger" in msg
    assert "LOSS" in msg
    assert "35" in msg


def test_user_message_contains_performance_section():
    msg = build_user_message(_make_metrics(), [])
    assert "PERFORMANCE" in msg
    assert "GPM" in msg


def test_user_message_contains_core_item():
    msg = build_user_message(_make_metrics(), [])
    assert "First core" in msg


def test_user_message_contains_nw_delta():
    msg = build_user_message(_make_metrics(), [])
    assert "Net worth delta" in msg


def test_user_message_contains_teamfight_section_when_data_present():
    msg = build_user_message(_make_metrics(teamfight_participation_rate=0.35), [])
    assert "TEAMFIGHTS" in msg
    assert "35%" in msg


def test_user_message_shows_na_when_teamfight_none():
    msg = build_user_message(_make_metrics(teamfight_participation_rate=None), [])
    assert "TEAMFIGHTS" in msg
    assert "N/A" in msg


def test_user_message_contains_detected_issues_section():
    msg = build_user_message(_make_metrics(), _sample_errors())
    assert "DETECTED ISSUES" in msg
    assert "CRITICAL" in msg


def test_user_message_limits_detected_issues_to_three():
    errors = _sample_errors() + [
        DetectedError(
            category="Extra",
            description="Extra issue",
            severity="medium",
            metric_value="n/a",
            threshold="n/a",
        )
    ]
    msg = build_user_message(_make_metrics(), errors)
    # Only first 3 should appear
    assert msg.count("[CRITICAL]") + msg.count("[HIGH]") + msg.count("[MEDIUM]") <= 3


# ---------------------------------------------------------------------------
# Death timestamps in message
# ---------------------------------------------------------------------------

def test_user_message_includes_death_timestamps_when_present():
    msg = build_user_message(_make_metrics(deaths_before_10=2, death_timestamps_laning=[3.83, 8.0]), [])
    assert "3.8" in msg
    assert "8.0" in msg


def test_user_message_no_timestamp_detail_when_zero_deaths():
    msg = build_user_message(_make_metrics(deaths_before_10=0, death_timestamps_laning=[]), [])
    assert "Deaths before 10 min: 0\n" in msg or "Deaths before 10 min: 0" in msg
    # No parenthetical timestamp detail
    assert "(at" not in msg


# ---------------------------------------------------------------------------
# Core item edge cases
# ---------------------------------------------------------------------------

def test_user_message_handles_no_core_item():
    msg = build_user_message(_make_metrics(first_core_item_minute=None, first_core_item_name=None), [])
    assert "None purchased" in msg


# ---------------------------------------------------------------------------
# Net worth delta sign
# ---------------------------------------------------------------------------

def test_user_message_shows_negative_delta_when_behind():
    # Our NW 2800, enemy 4200 → delta = -1400
    msg = build_user_message(_make_metrics(net_worth_at_10=2800, enemy_carry_net_worth_at_10=4200), [])
    assert "-1400" in msg


def test_user_message_shows_positive_delta_when_ahead():
    msg = build_user_message(_make_metrics(net_worth_at_10=5000, enemy_carry_net_worth_at_10=3000), [])
    assert "+2000" in msg


# ---------------------------------------------------------------------------
# Token budget
# ---------------------------------------------------------------------------

def test_token_budget_not_exceeded_for_typical_match():
    metrics = _make_metrics()
    errors = _sample_errors()
    system = build_system_prompt()
    user = build_user_message(metrics, errors)
    # Approximate: 1 token ≈ 4 chars
    estimated = len(system) // 4 + len(user) // 4
    assert estimated <= _TOKEN_BUDGET, (
        f"Estimated tokens ({estimated}) exceed budget ({_TOKEN_BUDGET})"
    )


def test_token_budget_warning_is_raised_when_exceeded():
    # Build a very large message by stuffing deaths
    metrics = _make_metrics(
        deaths_before_10=9,
        death_timestamps_laning=[float(i) for i in range(9)],
    )
    # Patch the message to be artificially large
    import dota_coach.prompt as p_mod

    original = p_mod._TOKEN_BUDGET
    try:
        p_mod._TOKEN_BUDGET = 1  # force budget to 1 token to trigger warning
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            build_user_message(metrics, [])
        assert any("tokens" in str(w.message).lower() for w in caught)
    finally:
        p_mod._TOKEN_BUDGET = original


# ---------------------------------------------------------------------------
# v2: Role-aware system prompt
# ---------------------------------------------------------------------------

def test_system_prompt_role_2_says_mid():
    s = build_system_prompt(role=2)
    assert "mid" in s.lower()
    assert "position 2" in s.lower()


def test_system_prompt_role_5_says_hard_support():
    s = build_system_prompt(role=5)
    assert "hard support" in s.lower()


def test_system_prompt_includes_few_shot_example():
    s = build_system_prompt(role=1)
    assert "EXAMPLE" in s


def test_system_prompt_each_role_has_different_example():
    s1 = build_system_prompt(role=1)
    s2 = build_system_prompt(role=2)
    assert s1 != s2


# ---------------------------------------------------------------------------
# v2: Role-specific user message rendering
# ---------------------------------------------------------------------------

def test_user_message_pos1_shows_nw_delta():
    msg = build_user_message(_make_metrics(), [], role=1)
    assert "Net worth delta" in msg


def test_user_message_pos5_shows_ward_placements():
    msg = build_user_message(_make_metrics(ward_placements=8), [], role=5)
    assert "Ward placements" in msg


def test_user_message_pos3_shows_stacks():
    msg = build_user_message(_make_metrics(stacks_created=3), [], role=3)
    assert "Stacks created" in msg


def test_user_message_with_enrichment_shows_patch_context():
    enrichment = EnrichmentContext(
        patch_name="7.38c",
        benchmarks=[
            HeroBenchmark(metric="gold_per_min", player_value=400, player_pct=0.5, bracket_avg=420),
        ],
        item_costs={"item_battle_fury": 4600},
        hero_base_stats={"base_attack_min": 46, "base_attack_max": 51, "move_speed": 285},
    )
    msg = build_user_message(_make_metrics(), [], role=1, enrichment=enrichment)
    assert "PATCH CONTEXT" in msg
    assert "4600g" in msg


def test_user_message_with_enrichment_shows_percentile():
    enrichment = EnrichmentContext(
        patch_name="7.38c",
        benchmarks=[
            HeroBenchmark(metric="gold_per_min", player_value=342, player_pct=0.25, bracket_avg=420),
        ],
        item_costs={},
        hero_base_stats={},
    )
    msg = build_user_message(_make_metrics(), [], role=1, enrichment=enrichment)
    assert "25%" in msg or "pct" in msg


def test_user_message_no_enrichment_no_patch_context():
    msg = build_user_message(_make_metrics(), [], role=1)
    assert "PATCH CONTEXT" not in msg


def test_token_budget_under_1200_for_all_roles():
    """v2 PRD says target under 1200 input tokens."""
    enrichment = EnrichmentContext(
        patch_name="7.38c",
        benchmarks=[
            HeroBenchmark(metric="gold_per_min", player_value=400, player_pct=0.50, bracket_avg=420),
            HeroBenchmark(metric="last_hits_per_min", player_value=5.0, player_pct=0.45, bracket_avg=5.5),
        ],
        item_costs={"item_battle_fury": 4600, "item_manta": 4900},
        hero_base_stats={"base_attack_min": 46, "base_attack_max": 51, "move_speed": 285},
    )
    for role in (1, 2, 3, 4, 5):
        system = build_system_prompt(role=role)
        user = build_user_message(_make_metrics(), _sample_errors(), role=role, enrichment=enrichment)
        estimated = len(system) // 4 + len(user) // 4
        assert estimated <= 1200, f"Role {role}: {estimated} tokens > 1200"
