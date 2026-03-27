"""Tests for build_chat_messages() in dota_coach/prompt.py."""
from __future__ import annotations

import pytest

from dota_coach.models import ChatRequest, ChatTurn, DetectedError, MatchMetrics, MatchReport
from dota_coach.prompt import build_chat_messages, _build_chat_system_prompt


# ---------------------------------------------------------------------------
# Fixture helper
# ---------------------------------------------------------------------------

def _base_metrics(**overrides) -> MatchMetrics:
    defaults = dict(
        match_id=999,
        hero="Juggernaut",
        duration_minutes=32.0,
        result="loss",
        lh_at_10=48,
        denies_at_10=4,
        deaths_before_10=1,
        death_timestamps_laning=[3.5],
        net_worth_at_10=3400,
        opponent_net_worth_at_10=3100,
        net_worth_at_20=7800,
        opponent_net_worth_at_20=7500,
        gpm=380,
        xpm=420,
        first_core_item_minute=14.5,
        first_core_item_name="item_battle_fury",
        laning_heatmap_own_half_pct=0.55,
        ward_purchases=0,
        teamfight_participation_rate=0.50,
        teamfight_avg_damage_contribution=0.18,
        first_roshan_minute=None,
        first_tower_minute=None,
    )
    defaults.update(overrides)
    return MatchMetrics(**defaults)


def _make_chat_request(
    *,
    role: int = 1,
    history: list[ChatTurn] | None = None,
    user_message: str = "What should I improve?",
    timeline: str = "0:00 Creeps spawn\n10:00 Tower falls",
    coaching_report: str = "You died too early in lane. Focus on safe CS.",
    errors: list[DetectedError] | None = None,
) -> ChatRequest:
    from dota_coach.role import ROLE_LABELS

    metrics = _base_metrics()
    report = MatchReport(
        match_id=metrics.match_id,
        hero=metrics.hero,
        role=role,
        role_label=ROLE_LABELS.get(role, "carry"),
        result=metrics.result,
        duration_minutes=metrics.duration_minutes,
        patch="7.36",
        metrics=metrics,
        errors=errors or [],
        coaching_report=coaching_report,
        timeline=timeline,
    )
    return ChatRequest(
        match_context=report,
        history=history or [],
        user_message=user_message,
    )


# ---------------------------------------------------------------------------
# 1. Returns list starting with system msg containing match context + timeline
# ---------------------------------------------------------------------------

def test_first_message_is_system_with_match_context():
    req = _make_chat_request()
    msgs = build_chat_messages(req)

    assert msgs[0]["role"] == "system"
    content = msgs[0]["content"]
    # Match context present
    assert "Juggernaut" in content
    assert "pos 1" in content
    # Timeline block present
    assert "MATCH TIMELINE:" in content
    assert "Creeps spawn" in content


# ---------------------------------------------------------------------------
# 2. History truncation — 21 turns keeps system, assistant, last 10, user_msg
# ---------------------------------------------------------------------------

def test_history_truncation_at_21_turns():
    turns = [
        ChatTurn(role="user" if i % 2 == 0 else "assistant", content=f"turn-{i}")
        for i in range(21)
    ]
    req = _make_chat_request(history=turns)
    msgs = build_chat_messages(req)

    # Structure: system + assistant + truncated_history(10) + user_message = 13
    assert msgs[0]["role"] == "system"
    assert msgs[1]["role"] == "assistant"
    assert msgs[-1]["role"] == "user"
    assert msgs[-1]["content"] == "What should I improve?"

    # The 10 kept history turns should be the *last* 10 (turn-11..turn-20)
    history_portion = msgs[2:-1]
    assert len(history_portion) == 10
    assert history_portion[0]["content"] == "turn-11"
    assert history_portion[-1]["content"] == "turn-20"


def test_history_at_20_is_not_truncated():
    turns = [
        ChatTurn(role="user" if i % 2 == 0 else "assistant", content=f"turn-{i}")
        for i in range(20)
    ]
    req = _make_chat_request(history=turns)
    msgs = build_chat_messages(req)

    # system + assistant + 20 history + user_message = 23
    history_portion = msgs[2:-1]
    assert len(history_portion) == 20
    assert history_portion[0]["content"] == "turn-0"


# ---------------------------------------------------------------------------
# 3. Empty timeline omitted with fallback note
# ---------------------------------------------------------------------------

def test_empty_timeline_shows_fallback():
    req = _make_chat_request(timeline="")
    msgs = build_chat_messages(req)

    content = msgs[0]["content"]
    assert "MATCH TIMELINE:" not in content
    assert "Timeline not available" in content


# ---------------------------------------------------------------------------
# 4. Role-aware persona — pos 1 (carry) vs pos 5 (hard support)
# ---------------------------------------------------------------------------

def test_persona_carry():
    req = _make_chat_request(role=1)
    content = msgs_system(req)
    assert "carry" in content.lower()


def test_persona_hard_support():
    req = _make_chat_request(role=5)
    content = msgs_system(req)
    assert "hard support" in content.lower()


def test_persona_mid():
    req = _make_chat_request(role=2)
    content = msgs_system(req)
    assert "mid" in content.lower()


def msgs_system(req: ChatRequest) -> str:
    return build_chat_messages(req)[0]["content"]


# ---------------------------------------------------------------------------
# 5. user_message appended as last user turn
# ---------------------------------------------------------------------------

def test_user_message_is_last():
    req = _make_chat_request(user_message="How do I farm faster?")
    msgs = build_chat_messages(req)

    assert msgs[-1] == {"role": "user", "content": "How do I farm faster?"}


def test_user_message_after_history():
    turns = [
        ChatTurn(role="user", content="first"),
        ChatTurn(role="assistant", content="reply"),
    ]
    req = _make_chat_request(history=turns, user_message="follow-up")
    msgs = build_chat_messages(req)

    assert msgs[-1]["content"] == "follow-up"
    assert msgs[-2]["content"] == "reply"


# ---------------------------------------------------------------------------
# 6. No real HTTP calls — all fixtures are inline (implicitly tested above)
# ---------------------------------------------------------------------------

def test_coaching_report_in_assistant_turn():
    req = _make_chat_request(coaching_report="Focus on BKB timing.")
    msgs = build_chat_messages(req)

    assert msgs[1] == {"role": "assistant", "content": "Focus on BKB timing."}


def test_errors_in_system_prompt():
    errs = [
        DetectedError(
            category="Poor laning CS",
            description="LH at 10 min is 30",
            severity="high",
            metric_value="30",
            threshold="45",
            player_pct=0.12,
        ),
    ]
    req = _make_chat_request(errors=errs)
    content = msgs_system(req)
    assert "DETECTED ERRORS:" in content
    assert "LH at 10 min is 30" in content


def test_patch_in_system_prompt():
    req = _make_chat_request()
    content = msgs_system(req)
    assert "PATCH: 7.36" in content
