"""Tests for prompt.py: token budget and message format (v2: role-aware)."""
from __future__ import annotations

import warnings

import pytest

from dota_coach.models import DetectedError, EnrichmentContext, HeroBenchmark, MatchMetrics
from dota_coach.prompt import build_system_prompt, build_user_message, _lane_line, _build_chat_system_prompt
from dota_coach.models import ChatRequest, MatchReport

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
        opponent_net_worth_at_10=4200,
        net_worth_at_20=6200,
        opponent_net_worth_at_20=9500,
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
    assert "NW delta" in msg


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
    msg = build_user_message(_make_metrics(net_worth_at_10=2800, opponent_net_worth_at_10=4200), [])
    assert "-1400" in msg


def test_user_message_shows_positive_delta_when_ahead():
    msg = build_user_message(_make_metrics(net_worth_at_10=5000, opponent_net_worth_at_10=3000), [])
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
    assert "NW delta" in msg


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


# ---------------------------------------------------------------------------
# Lane matchup line
# ---------------------------------------------------------------------------

def test_lane_line_returns_none_when_no_enemies():
    m = _make_metrics(lane_enemies=[], lane_allies=[])
    assert _lane_line(m) is None


def test_lane_line_mid_no_partners():
    m = _make_metrics(hero="Shadow Fiend", lane_enemies=["Lina"], lane_allies=[])
    result = _lane_line(m)
    assert result == "- Lane: Shadow Fiend vs Lina"


def test_lane_line_with_allies_and_enemies():
    m = _make_metrics(hero="Juggernaut", lane_enemies=["Axe", "Pudge"], lane_allies=["Lion"])
    result = _lane_line(m)
    assert result == "- Lane: Juggernaut + Lion vs Axe + Pudge"


def test_lane_line_multiple_enemies_no_ally():
    m = _make_metrics(hero="Tidehunter", lane_enemies=["Phantom Assassin", "Shadow Shaman"], lane_allies=["Witch Doctor"])
    result = _lane_line(m)
    assert result == "- Lane: Tidehunter + Witch Doctor vs Phantom Assassin + Shadow Shaman"


def test_user_message_shows_lane_line_when_enemies_present():
    m = _make_metrics(hero="Anti-Mage", lane_enemies=["Axe", "Crystal Maiden"], lane_allies=["Io"])
    msg = build_user_message(m, [], role=1)
    assert "- Lane: Anti-Mage + Io vs Axe + Crystal Maiden" in msg


def test_user_message_lane_line_is_first_in_performance_block():
    m = _make_metrics(hero="Anti-Mage", lane_enemies=["Axe"], lane_allies=[])
    msg = build_user_message(m, [], role=1)
    perf_idx = msg.index("PERFORMANCE")
    lane_idx = msg.index("- Lane:")
    gpm_idx = msg.index("- GPM:")
    assert perf_idx < lane_idx < gpm_idx


def test_user_message_no_lane_line_when_no_enemies():
    m = _make_metrics(lane_enemies=[], lane_allies=[])
    msg = build_user_message(m, [], role=1)
    assert "- Lane:" not in msg


def test_user_message_lane_line_all_roles():
    """Lane line should appear for all roles when lane_enemies is set."""
    for role in (1, 2, 3, 4, 5):
        m = _make_metrics(hero="Lion", lane_enemies=["Axe"], lane_allies=[], ward_placements=5)
        msg = build_user_message(m, [], role=role)
        assert "- Lane: Lion vs Axe" in msg, f"Lane line missing for role {role}"


def _make_match_report(metrics: MatchMetrics) -> "MatchReport":
    return MatchReport(
        match_id=metrics.match_id,
        hero=metrics.hero,
        role=1,
        role_label="carry",
        result=metrics.result,
        duration_minutes=metrics.duration_minutes,
        metrics=metrics,
        coaching_report="Test report.",
    )


def test_chat_system_prompt_includes_lane_line_when_enemies_present():
    m = _make_metrics(hero="Juggernaut", lane_enemies=["Axe", "Pudge"], lane_allies=["Lion"])
    report = _make_match_report(m)
    content = _build_chat_system_prompt(report)
    assert "- Lane: Juggernaut + Lion vs Axe + Pudge" in content


def test_chat_system_prompt_omits_lane_line_when_no_enemies():
    m = _make_metrics(lane_enemies=[], lane_allies=[])
    report = _make_match_report(m)
    content = _build_chat_system_prompt(report)
    assert "- Lane:" not in content


# ---------------------------------------------------------------------------
# Lane matchup win rate formatting
# ---------------------------------------------------------------------------

def test_lane_line_wr_shown_for_enemy_with_winrate():
    """Enemy with WR entry shows '(48.2% WR)' format."""
    m = _make_metrics(
        hero="Juggernaut",
        lane_enemies=["Axe"],
        lane_allies=[],
        lane_matchup_winrates={"Axe": 0.482},
    )
    result = _lane_line(m)
    assert result == "- Lane: Juggernaut vs Axe (48.2% WR)"


def test_lane_line_wr_fallback_plain_name_when_no_winrate():
    """Enemy without WR entry shows plain name (fallback)."""
    m = _make_metrics(
        hero="Juggernaut",
        lane_enemies=["Axe"],
        lane_allies=[],
        lane_matchup_winrates={},
    )
    result = _lane_line(m)
    assert result == "- Lane: Juggernaut vs Axe"


def test_lane_line_unfavorable_label_when_avg_wr_below_47():
    """avg WR < 0.47 appends ' — unfavorable lane'."""
    m = _make_metrics(
        hero="Juggernaut",
        lane_enemies=["Axe", "Pudge"],
        lane_allies=["Lion"],
        lane_matchup_winrates={"Axe": 0.42, "Pudge": 0.45},
    )
    result = _lane_line(m)
    assert result is not None
    assert "\u2014 unfavorable lane" in result


def test_lane_line_no_unfavorable_label_when_avg_wr_at_or_above_47():
    """avg WR >= 0.47 does NOT append the unfavorable label."""
    m = _make_metrics(
        hero="Juggernaut",
        lane_enemies=["Axe", "Pudge"],
        lane_allies=["Lion"],
        lane_matchup_winrates={"Axe": 0.50, "Pudge": 0.48},
    )
    result = _lane_line(m)
    assert result is not None
    assert "unfavorable" not in result


def test_lane_line_mixed_wr_one_with_one_without():
    """One enemy has WR, one does not — WR shown only for the known enemy."""
    m = _make_metrics(
        hero="Juggernaut",
        lane_enemies=["Axe", "Pudge"],
        lane_allies=["Lion"],
        lane_matchup_winrates={"Axe": 0.531},
    )
    result = _lane_line(m)
    assert result is not None
    assert "Axe (53.1% WR)" in result
    assert "Pudge" in result
    # Pudge should appear without WR annotation
    assert "Pudge (" not in result


def test_lane_line_wr_empty_map_no_unfavorable_label():
    """When lane_matchup_winrates is empty, no unfavorable label is added."""
    m = _make_metrics(
        hero="Juggernaut",
        lane_enemies=["Axe"],
        lane_allies=[],
        lane_matchup_winrates={},
    )
    result = _lane_line(m)
    assert result == "- Lane: Juggernaut vs Axe"
    assert "unfavorable" not in result


def test_lane_line_wr_in_chat_system_prompt():
    """WR annotations appear in the chat system prompt recap when winrates are set."""
    m = _make_metrics(
        hero="Juggernaut",
        lane_enemies=["Axe", "Pudge"],
        lane_allies=["Lion"],
        lane_matchup_winrates={"Axe": 0.482, "Pudge": 0.531},
    )
    report = _make_match_report(m)
    content = _build_chat_system_prompt(report)
    assert "Axe (48.2% WR)" in content
    assert "Pudge (53.1% WR)" in content


# ---------------------------------------------------------------------------
# Lane ally synergy score formatting
# ---------------------------------------------------------------------------

def test_lane_line_synergy_score_shown_for_ally():
    """Ally with synergy score shows '(synergy +8.3)' annotation."""
    m = _make_metrics(
        hero="Juggernaut",
        lane_enemies=["Axe"],
        lane_allies=["Lion"],
        lane_ally_synergy_scores={"Lion": 8.3},
    )
    result = _lane_line(m)
    assert result is not None
    assert "Lion (synergy +8.3)" in result


def test_lane_line_negative_synergy_score_shown_for_ally():
    """Ally with negative synergy score shows '(synergy -3.1)' annotation."""
    m = _make_metrics(
        hero="Juggernaut",
        lane_enemies=["Axe"],
        lane_allies=["Techies"],
        lane_ally_synergy_scores={"Techies": -3.1},
    )
    result = _lane_line(m)
    assert result is not None
    assert "Techies (synergy -3.1)" in result


def test_lane_line_synergy_omitted_when_no_synergy_map():
    """No synergy scores → ally shown as plain name, no synergy annotation."""
    m = _make_metrics(
        hero="Juggernaut",
        lane_enemies=["Axe", "Pudge"],
        lane_allies=["Lion"],
        lane_ally_synergy_scores={},
    )
    result = _lane_line(m)
    assert result == "- Lane: Juggernaut + Lion vs Axe + Pudge"


def test_lane_line_good_synergy_label_when_score_above_5():
    """avg ally synergy > 5 appends ' — good lane synergy'."""
    m = _make_metrics(
        hero="Juggernaut",
        lane_enemies=["Axe"],
        lane_allies=["Lion"],
        lane_ally_synergy_scores={"Lion": 7.0},
    )
    result = _lane_line(m)
    assert result is not None
    assert "\u2014 good lane synergy" in result


def test_lane_line_weak_synergy_label_when_score_below_neg3():
    """avg ally synergy < -3 appends ' — weak synergy'."""
    m = _make_metrics(
        hero="Juggernaut",
        lane_enemies=["Axe"],
        lane_allies=["Techies"],
        lane_ally_synergy_scores={"Techies": -5.0},
    )
    result = _lane_line(m)
    assert result is not None
    assert "\u2014 weak synergy" in result


def test_lane_line_no_context_label_for_neutral_synergy():
    """Synergy score between -3 and 5 adds no context label."""
    m = _make_metrics(
        hero="Juggernaut",
        lane_enemies=["Axe"],
        lane_allies=["Lion"],
        lane_ally_synergy_scores={"Lion": 2.5},
    )
    result = _lane_line(m)
    assert result is not None
    assert "synergy" in result  # score annotation present
    assert "good lane synergy" not in result
    assert "weak synergy" not in result


def test_lane_line_synergy_overrides_unfavorable_label():
    """When synergy data is present, unfavorable-lane WR check is skipped."""
    m = _make_metrics(
        hero="Juggernaut",
        lane_enemies=["Axe", "Pudge"],
        lane_allies=["Lion"],
        lane_matchup_winrates={"Axe": 0.40, "Pudge": 0.42},
        lane_ally_synergy_scores={"Lion": 2.5},
    )
    result = _lane_line(m)
    assert result is not None
    assert "unfavorable" not in result


def test_lane_line_full_format_with_synergy_and_wr():
    """Full format: ally with synergy score + enemies with WR."""
    m = _make_metrics(
        hero="Juggernaut",
        lane_enemies=["Axe", "Pudge"],
        lane_allies=["Lion"],
        lane_matchup_winrates={"Axe": 0.482, "Pudge": 0.531},
        lane_ally_synergy_scores={"Lion": 8.3},
    )
    result = _lane_line(m)
    assert result == "- Lane: Juggernaut + Lion (synergy +8.3) vs Axe (48.2% WR) + Pudge (53.1% WR) \u2014 good lane synergy"


from dota_coach.models import LocalBenchmark, LocalBenchmarkProgress


def _make_metrics_for_prompt():
    from dota_coach.models import MatchMetrics
    return MatchMetrics(
        match_id=1, hero="Anti-Mage", duration_minutes=35.0, result="loss",
        lh_at_10=60, denies_at_10=5, deaths_before_10=0,
        death_timestamps_laning=[], net_worth_at_10=8000, net_worth_at_20=16000,
        opponent_net_worth_at_10=7500, opponent_net_worth_at_20=15000,
        gpm=400, xpm=550, total_last_hits=180,
        first_core_item_minute=None, first_core_item_name=None,
        laning_heatmap_own_half_pct=0.4, ward_purchases=0,
        teamfight_participation_rate=0.6, teamfight_avg_damage_contribution=None,
        first_roshan_minute=None, first_tower_minute=None, turbo=False,
    )


def _make_enrichment_with_local_benchmarks():
    from dota_coach.models import EnrichmentContext
    return EnrichmentContext(
        patch_name="7.37",
        benchmarks=[],
        item_costs={},
        hero_base_stats={},
        local_benchmarks=[
            LocalBenchmark(metric="gold_per_min", player_value=400.0,
                           player_pct=0.43, p25=350.0, median=480.0,
                           p75=560.0, sample_size=45),
        ],
    )


def _make_enrichment_with_progress():
    from dota_coach.models import EnrichmentContext
    return EnrichmentContext(
        patch_name="7.37",
        benchmarks=[],
        item_costs={},
        hero_base_stats={},
        local_benchmark_progress=LocalBenchmarkProgress(
            hero="Anti-Mage", matches_stored=12, threshold=30
        ),
    )


class TestLocalBenchmarkPrompt:
    def test_local_benchmark_block_appears_when_benchmarks_present(self):
        """build_user_message includes LOCAL BENCHMARKS block when local_benchmarks non-empty."""
        from dota_coach.prompt import build_user_message
        msg = build_user_message(
            _make_metrics_for_prompt(), [],
            role=1, enrichment=_make_enrichment_with_local_benchmarks()
        )
        assert "LOCAL BENCHMARKS" in msg
        assert "Anti-Mage" in msg
        assert "43%" in msg  # player_pct

    def test_progress_line_appears_when_below_threshold(self):
        """build_user_message includes progress line when local_benchmark_progress is set."""
        from dota_coach.prompt import build_user_message
        msg = build_user_message(
            _make_metrics_for_prompt(), [],
            role=1, enrichment=_make_enrichment_with_progress()
        )
        assert "LOCAL BENCHMARKS" in msg
        assert "12/30" in msg
        assert "Anti-Mage" in msg

    def test_no_local_benchmark_block_when_absent(self):
        """No LOCAL BENCHMARKS section when enrichment has neither local_benchmarks nor progress."""
        from dota_coach.prompt import build_user_message
        from dota_coach.models import EnrichmentContext
        enr = EnrichmentContext(patch_name="7.37", benchmarks=[], item_costs={}, hero_base_stats={})
        msg = build_user_message(_make_metrics_for_prompt(), [], role=1, enrichment=enr)
        assert "LOCAL BENCHMARKS" not in msg
