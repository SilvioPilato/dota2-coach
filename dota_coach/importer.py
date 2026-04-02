"""Metrics-only import pipeline.

Fetches recent matches for an account, extracts metrics without running the LLM,
and saves MatchReport(metrics_only=True) to the history DB. Used to bootstrap
local benchmark data from past games.
"""
from __future__ import annotations

import logging

from dota_coach.extractor import extract_metrics_from_opendota
from dota_coach.enricher import enrich
from dota_coach.history import get_analyzed_ids, save_match_report
from dota_coach.models import MatchReport
from dota_coach.opendota import get_match, get_paginated_matches
from dota_coach.role import detect_role, ROLE_LABELS
from dota_coach.stratz import get_match_positions

logger = logging.getLogger(__name__)


async def import_match_metrics(account_id: int, limit: int = 50) -> dict:
    """Fetch up to `limit` recent matches for `account_id`, extract metrics, save to DB.

    Returns:
        {"imported": int, "skipped": int, "failed": int}
    """
    imported = skipped = failed = 0

    # Fetch recent match list from OpenDota
    try:
        matches = await get_paginated_matches(account_id, limit=limit)
    except Exception as exc:
        logger.error("Failed to fetch match list for account %s: %s", account_id, exc)
        return {"imported": 0, "skipped": 0, "failed": 0}

    # Skip matches already stored
    stored_ids = get_analyzed_ids(account_id)
    to_process = [m for m in matches if m["match_id"] not in stored_ids]
    skipped = len(matches) - len(to_process)

    for match_stub in to_process:
        match_id = match_stub["match_id"]
        try:
            match_meta = await get_match(match_id)

            # Role detection: Stratz first, fallback to detect_role
            stratz_positions = await get_match_positions(match_id)
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
            logger.info("Imported match %s for account %s (hero: %s)", match_id, account_id, metrics.hero)
            imported += 1

        except Exception as exc:
            logger.warning("Failed to import match %s: %s", match_id, exc)
            failed += 1

    return {"imported": imported, "skipped": skipped, "failed": failed}
