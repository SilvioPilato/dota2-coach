"""Metrics-only import pipeline.

Fetches recent matches for an account, extracts metrics without running the LLM,
and saves MatchReport(metrics_only=True) to the history DB. Used to bootstrap
local benchmark data from past games.

Rate limiting: 60 requests/minute (OpenDota free tier). This importer enforces
1-second delays between API calls and exponential backoff on 429 errors.
"""
from __future__ import annotations

import asyncio
import logging

from dota_coach.extractor import extract_metrics_from_opendota
from dota_coach.enricher import enrich
from dota_coach.history import get_analyzed_ids, save_match_report
from dota_coach.models import MatchReport
from dota_coach.opendota import get_match, get_paginated_matches
from dota_coach.role import detect_role, ROLE_LABELS
from dota_coach.stratz import get_match_positions

logger = logging.getLogger(__name__)

# Rate limiting: OpenDota free tier = 60 requests/minute
_RATE_LIMIT_DELAY = 1.0  # seconds between API calls (keeps ~50-55 req/min)
_BACKOFF_BASE = 2.0      # exponential backoff multiplier


async def _call_with_backoff(coro, match_id: int | None = None, attempt: int = 0) -> dict:
    """Execute an API call with exponential backoff on 429 (Too Many Requests).

    Args:
        coro: The awaitable (API call) to execute
        match_id: Optional match ID for logging
        attempt: Current retry attempt (0 on first call)

    Returns:
        The API response dict

    Raises:
        Exception: If all retries exhausted or non-429 error occurs
    """
    max_retries = 3
    try:
        return await coro
    except Exception as exc:
        error_str = str(exc)
        is_429 = "429" in error_str or "too many" in error_str.lower()

        if is_429 and attempt < max_retries:
            wait_time = _BACKOFF_BASE ** attempt
            label = f"match {match_id}" if match_id else "request"
            logger.warning(
                "Rate limited on %s (attempt %d), retrying in %.1fs",
                label, attempt + 1, wait_time
            )
            await asyncio.sleep(wait_time)
            return await _call_with_backoff(coro, match_id, attempt + 1)

        raise


async def import_match_metrics(account_id: int, limit: int = 50, offset: int = 0) -> dict:
    """Fetch up to `limit` recent matches for `account_id`, extract metrics, save to DB.

    Args:
        account_id: OpenDota account ID (Steam3 format)
        limit: Maximum number of matches to fetch per batch (default 50)
        offset: Skip first N matches for pagination (0=newest, 150=next batch, etc.)

    Rate limiting: enforces 1-second delays between API calls to stay under OpenDota's
    60 req/minute free tier limit. Retries with exponential backoff on 429 errors.

    Returns:
        {"imported": int, "skipped": int, "failed": int}

    Example:
        # Batch 1: newest 150 matches
        await import_match_metrics(123, limit=150, offset=0)
        # Batch 2: next 150 older matches
        await import_match_metrics(123, limit=150, offset=150)
        # Batch 3: next 150 older matches
        await import_match_metrics(123, limit=150, offset=300)
    """
    imported = skipped = failed = 0

    # Fetch match list from OpenDota with pagination
    try:
        matches = await get_paginated_matches(account_id, limit=limit, offset=offset)
    except Exception as exc:
        logger.error("Failed to fetch match list for account %s (offset %d): %s", account_id, offset, exc)
        return {"imported": 0, "skipped": 0, "failed": 0}

    # Skip matches already stored
    stored_ids = get_analyzed_ids(account_id)
    to_process = [m for m in matches if m["match_id"] not in stored_ids]
    skipped = len(matches) - len(to_process)

    logger.info(
        "Importing %d matches for account %s (skipping %d already stored)",
        len(to_process), account_id, skipped
    )

    for idx, match_stub in enumerate(to_process):
        match_id = match_stub["match_id"]

        # Rate limiting: enforce 1-second delay between API calls
        if idx > 0:
            await asyncio.sleep(_RATE_LIMIT_DELAY)

        try:
            # Fetch full match data with backoff
            match_meta = await _call_with_backoff(
                get_match(match_id), match_id=match_id
            )

            # Rate limiting between calls
            await asyncio.sleep(_RATE_LIMIT_DELAY)

            # Role detection: Stratz first, fallback to detect_role
            stratz_positions = await _call_with_backoff(
                get_match_positions(match_id), match_id=match_id
            )
            if stratz_positions and account_id in stratz_positions:
                role = stratz_positions[account_id]
            else:
                role = detect_role(match_meta, account_id)

            role_label = ROLE_LABELS.get(role, "carry")

            metrics = extract_metrics_from_opendota(account_id, match_meta)
            enrichment = await enrich(metrics, match_meta, account_id=account_id)

            report = MatchReport(
                match_id=match_id,
                hero=metrics.hero,
                role=role,
                role_label=role_label,
                result=metrics.result,
                duration_minutes=metrics.duration_minutes,
                patch=enrichment.patch_name,
                turbo=metrics.turbo,
                degraded=True,  # always degraded — no replay
                metrics=metrics,
                benchmarks=enrichment.benchmarks,
                errors=[],
                coaching_report="",
                priority_focus="",
                timeline="",
                local_benchmarks=enrichment.local_benchmarks,
                local_benchmark_progress=enrichment.local_benchmark_progress,
                metrics_only=True,
            )

            save_match_report(match_id, account_id, role, report.model_dump())
            logger.info(
                "Imported match %s for account %s (hero: %s, %d/%d)",
                match_id, account_id, metrics.hero, imported + 1, len(to_process)
            )
            imported += 1

        except Exception as exc:
            logger.warning("Failed to import match %s: %s", match_id, exc)
            failed += 1

    logger.info(
        "Import complete for account %s: %d imported, %d skipped, %d failed",
        account_id, imported, skipped, failed
    )
    return {"imported": imported, "skipped": skipped, "failed": failed}
