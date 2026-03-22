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
async def download_and_decompress(
    replay_url: str | None,
    match_id: int,
    force_redownload: bool = False,
):
    """
    Yield a Path to a decompressed .dem file, using a persistent disk cache.
    If the .dem is already cached and within TTL, yields it directly (no download).
    Otherwise downloads from Valve CDN, decompresses, and writes atomically to cache.
    The cached .dem is never deleted on context exit.
    """
    from dota_coach import cache

    if force_redownload:
        cache.invalidate_dem_cache(match_id)

    if cache.is_dem_cache_fresh(match_id):
        yield cache.get_dem_cache_path(match_id)
        return

    # No fresh cached file — must download.
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

    dem_cache_path = cache.get_dem_cache_path(match_id)  # also ensures CACHE_DIR exists
    tmp_path = dem_cache_path.with_suffix(".tmp")

    with tempfile.TemporaryDirectory() as tmp_dir:
        bz2_path = Path(tmp_dir) / "replay.dem.bz2"

        async with httpx.AsyncClient(timeout=300.0) as client:
            async with client.stream("GET", replay_url) as response:
                response.raise_for_status()
                with open(bz2_path, "wb") as f:
                    async for chunk in response.aiter_bytes(chunk_size=1024 * 1024):
                        f.write(chunk)

        # Decompress into CACHE_DIR atomically: write .tmp then rename
        with bz2.open(bz2_path, "rb") as f_in, open(tmp_path, "wb") as f_out:
            while chunk := f_in.read(4 * 1024 * 1024):
                f_out.write(chunk)

    tmp_path.rename(dem_cache_path)
    yield dem_cache_path
