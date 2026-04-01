"""Tests for dota_coach.opendota — get_paginated_matches."""
from __future__ import annotations

import pytest
import httpx
from unittest.mock import AsyncMock, patch, MagicMock

from dota_coach.opendota import get_paginated_matches


@pytest.mark.asyncio
async def test_get_paginated_matches_default_params():
    """Calls /players/{id}/matches with limit=20 and offset=0 by default."""
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = [{"match_id": 1}, {"match_id": 2}]

    with patch("dota_coach.opendota.httpx.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await get_paginated_matches(account_id=12345678)

    mock_client.get.assert_called_once_with(
        "https://api.opendota.com/api/players/12345678/matches",
        params={"limit": 20, "offset": 0, "project": ["game_mode", "lobby_type"]},
    )
    assert result == [{"match_id": 1}, {"match_id": 2}]


@pytest.mark.asyncio
async def test_get_paginated_matches_custom_offset():
    """Passes custom limit and offset to the API."""
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = []

    with patch("dota_coach.opendota.httpx.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await get_paginated_matches(account_id=12345678, limit=20, offset=40)

    mock_client.get.assert_called_once_with(
        "https://api.opendota.com/api/players/12345678/matches",
        params={"limit": 20, "offset": 40, "project": ["game_mode", "lobby_type"]},
    )
    assert result == []


@pytest.mark.asyncio
async def test_get_paginated_matches_raises_on_http_error():
    """Propagates HTTP errors via raise_for_status."""
    mock_response = MagicMock()
    mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "502", request=MagicMock(), response=MagicMock()
    )

    with patch("dota_coach.opendota.httpx.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

        with pytest.raises(httpx.HTTPStatusError):
            await get_paginated_matches(account_id=12345678)
