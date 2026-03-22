"""Tests for dota_coach.stratz — Stratz GraphQL position client."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dota_coach.stratz import get_match_positions

_STEAM64_BASE = 76561197960265728

# Steam64 IDs for two test players
STEAM_PLAYER_1 = _STEAM64_BASE + 123
STEAM_PLAYER_2 = _STEAM64_BASE + 456


def _make_response(players: list[dict]) -> MagicMock:
    mock = MagicMock()
    mock.json.return_value = {"data": {"match": {"players": players}}}
    mock.raise_for_status = MagicMock()
    return mock


def _run(coro):
    return asyncio.run(coro)


def test_returns_none_when_no_api_key(monkeypatch):
    monkeypatch.delenv("STRATZ_API_KEY", raising=False)
    assert _run(get_match_positions(12345)) is None


def test_returns_none_when_api_key_empty(monkeypatch):
    monkeypatch.setenv("STRATZ_API_KEY", "")
    assert _run(get_match_positions(12345)) is None


def test_returns_positions_steam64(monkeypatch):
    monkeypatch.setenv("STRATZ_API_KEY", "test-key")
    resp = _make_response([
        {"steamAccountId": STEAM_PLAYER_1, "position": "POSITION_1"},
        {"steamAccountId": STEAM_PLAYER_2, "position": "POSITION_5"},
    ])
    with patch("dota_coach.stratz.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=resp)
        mock_client_cls.return_value = mock_client

        result = _run(get_match_positions(99999))

    assert result == {123: 1, 456: 5}


def test_returns_positions_steam32(monkeypatch):
    """Stratz may return 32-bit IDs for some accounts; these pass through unchanged."""
    monkeypatch.setenv("STRATZ_API_KEY", "test-key")
    resp = _make_response([
        {"steamAccountId": 999, "position": "POSITION_3"},
    ])
    with patch("dota_coach.stratz.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=resp)
        mock_client_cls.return_value = mock_client

        result = _run(get_match_positions(99999))

    assert result == {999: 3}


def test_returns_none_on_http_error(monkeypatch):
    monkeypatch.setenv("STRATZ_API_KEY", "test-key")
    with patch("dota_coach.stratz.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=Exception("network error"))
        mock_client_cls.return_value = mock_client

        result = _run(get_match_positions(99999))

    assert result is None


def test_returns_none_on_empty_players(monkeypatch):
    monkeypatch.setenv("STRATZ_API_KEY", "test-key")
    resp = _make_response([])
    with patch("dota_coach.stratz.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=resp)
        mock_client_cls.return_value = mock_client

        result = _run(get_match_positions(99999))

    assert result is None


def test_skips_invalid_position_prefix(monkeypatch):
    monkeypatch.setenv("STRATZ_API_KEY", "test-key")
    resp = _make_response([
        {"steamAccountId": STEAM_PLAYER_1, "position": "UNKNOWN_POS"},
        {"steamAccountId": STEAM_PLAYER_2, "position": "POSITION_2"},
    ])
    with patch("dota_coach.stratz.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=resp)
        mock_client_cls.return_value = mock_client

        result = _run(get_match_positions(99999))

    assert result == {456: 2}


def test_returns_none_on_unexpected_response_shape(monkeypatch):
    monkeypatch.setenv("STRATZ_API_KEY", "test-key")
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"errors": [{"message": "not found"}]}
    mock_resp.raise_for_status = MagicMock()
    with patch("dota_coach.stratz.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        result = _run(get_match_positions(99999))

    assert result is None

