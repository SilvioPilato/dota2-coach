"""Valve CDN replay download + bz2 decompress."""
from __future__ import annotations

import bz2
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

import httpx


class ReplayExpiredError(Exception):
    pass


@asynccontextmanager
async def download_and_decompress(replay_url: str | None):
    """
    Download .dem.bz2 from Valve CDN, decompress, yield Path to .dem file.
    Cleans up automatically on context exit (including SIGINT).
    """
    if not replay_url:
        raise ReplayExpiredError(
            "Replay not available. Valve CDN replays expire after ~7 days."
        )

    # HEAD check before downloading
    async with httpx.AsyncClient(timeout=60.0) as client:
        head = await client.head(replay_url)
        if head.status_code in (403, 404):
            raise ReplayExpiredError(
                "Replay has expired on Valve CDN. Match too old to analyze."
            )
        if head.status_code != 200:
            raise RuntimeError(
                f"Unexpected CDN response {head.status_code} for URL: {replay_url}"
            )

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        bz2_path = tmp_path / "replay.dem.bz2"
        dem_path = tmp_path / "replay.dem"

        async with httpx.AsyncClient(timeout=300.0) as client:
            async with client.stream("GET", replay_url) as response:
                response.raise_for_status()
                with open(bz2_path, "wb") as f:
                    async for chunk in response.aiter_bytes(chunk_size=1024 * 1024):
                        f.write(chunk)

        with bz2.open(bz2_path, "rb") as f_in, open(dem_path, "wb") as f_out:
            while chunk := f_in.read(4 * 1024 * 1024):
                f_out.write(chunk)

        yield dem_path
