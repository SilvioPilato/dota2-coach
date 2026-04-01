"""Tests for GET /recent-matches/{account_id} endpoint."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from dota_coach.api import app, MatchSummary

client = TestClient(app)


# ---------------------------------------------------------------------------
# MatchSummary model tests
# ---------------------------------------------------------------------------

def test_match_summary_has_game_mode_and_lobby_type():
    """MatchSummary must include game_mode and lobby_type fields."""
    s = MatchSummary(
        match_id=1,
        hero_id=1,
        hero_name="Axe",
        start_time=1700000000,
        duration_seconds=1800,
        won=True,
        kills=5,
        deaths=2,
        assists=3,
        analyzed=False,
        replay_available=True,
        game_mode=1,
        lobby_type=7,
    )
    assert s.game_mode == 1
    assert s.lobby_type == 7


def test_match_summary_game_mode_defaults_to_zero():
    """game_mode and lobby_type default to 0 if not provided."""
    s = MatchSummary(
        match_id=1, hero_id=1, hero_name="", start_time=0,
        duration_seconds=0, won=False, kills=0, deaths=0, assists=0,
        analyzed=False, replay_available=False,
    )
    assert s.game_mode == 0
    assert s.lobby_type == 0


# ---------------------------------------------------------------------------
# Endpoint tests
# ---------------------------------------------------------------------------

FAKE_MATCH = {
    "match_id": 9000000001,
    "start_time": 1700000000,
    "player_slot": 0,
    "radiant_win": True,
    "hero_id": 2,
    "duration": 1800,
    "kills": 10,
    "deaths": 1,
    "assists": 5,
    "game_mode": 23,
    "lobby_type": 0,
}


def test_recent_matches_returns_game_mode_and_lobby_type():
    """Endpoint serializes game_mode and lobby_type into each row."""
    with patch("dota_coach.history.get_analyzed_ids", return_value=set()), \
         patch("dota_coach.opendota.get_paginated_matches", new=AsyncMock(return_value=[FAKE_MATCH])):
        resp = client.get("/recent-matches/12345678")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["game_mode"] == 23
    assert data[0]["lobby_type"] == 0


def test_recent_matches_offset_param_passed_through():
    """?offset=20 is forwarded to get_paginated_matches."""
    mock_fn = AsyncMock(return_value=[])
    with patch("dota_coach.history.get_analyzed_ids", return_value=set()), \
         patch("dota_coach.opendota.get_paginated_matches", new=mock_fn):
        client.get("/recent-matches/12345678?offset=20")

    mock_fn.assert_called_once_with(12345678, limit=20, offset=20)


def test_recent_matches_offset_defaults_to_zero():
    """When ?offset is absent, defaults to 0."""
    mock_fn = AsyncMock(return_value=[])
    with patch("dota_coach.history.get_analyzed_ids", return_value=set()), \
         patch("dota_coach.opendota.get_paginated_matches", new=mock_fn):
        client.get("/recent-matches/12345678")

    mock_fn.assert_called_once_with(12345678, limit=20, offset=0)
