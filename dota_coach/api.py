"""FastAPI backend — orchestrates the full analysis + coaching pipeline."""
from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from dota_coach.models import ChatRequest

load_dotenv()

app = FastAPI(title="Dota 2 Personal Coach", version="2.0")

# Serve static files (index.html, style.css)
_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
if _STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR), html=True), name="static")


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class AnalyzeRequest(BaseModel):
    match_id: str
    player_id: str | None = None
    role_override: int | None = None  # 1-5 to override auto-detected role
    force_reanalyze: bool = False    # skip analysis cache; re-run full pipeline (keep .dem)
    force_redownload: bool = False   # delete .dem and re-download; implies force_reanalyze


# ---------------------------------------------------------------------------
# POST /analyze
# ---------------------------------------------------------------------------

@app.post("/analyze")
async def analyze(req: AnalyzeRequest):
    """Run the full analysis pipeline and return the coaching report as JSON."""
    from dota_coach.cache import read_analysis_cache, write_analysis_cache
    from dota_coach.coach import CoachError, get_coaching
    from dota_coach.detector import detect_errors
    from dota_coach.downloader import ReplayExpiredError, download_and_decompress
    from dota_coach.enricher import enrich
    from dota_coach.extractor import build_timeline, extract_metrics
    from dota_coach.models import MatchReport
    from dota_coach.opendota import get_match
    from dota_coach.opendota import request_parse_and_wait
    from dota_coach.parser import ParserNotRunningError, check_sidecar_health, parse_replay
    from dota_coach.prompt import build_system_prompt, build_user_message
    from dota_coach.role import ROLE_LABELS, detect_role, get_role_profile

    # 1. Check parser sidecar
    try:
        check_sidecar_health()
    except ParserNotRunningError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    # 2. Fetch match metadata from OpenDota
    try:
        match_id_int = int(req.match_id)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid match_id: {req.match_id}")

    try:
        match_meta = await get_match(match_id_int)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=f"Match not found: {exc}")

    # 3. Identify our player
    if req.player_id:
        try:
            raw_id = int(req.player_id)
        except ValueError:
            raise HTTPException(status_code=422, detail=f"Invalid player_id: {req.player_id}")
        # Convert Steam64 ID to 32-bit account_id used by OpenDota
        _STEAM64_BASE = 76561197960265728
        account_id = raw_id - _STEAM64_BASE if raw_id > _STEAM64_BASE else raw_id
    else:
        # Use first player as fallback (for demo/testing)
        players = match_meta.get("players", [])
        if not players:
            raise HTTPException(status_code=422, detail="No players in match data")
        account_id = players[0].get("account_id", 0)

    # 4. Detect role (or use override)
    if req.role_override and 1 <= req.role_override <= 5:
        role = req.role_override
    else:
        from dota_coach.stratz import get_match_positions
        # Try Stratz first for accurate pos 1-5; fall back to heuristic if unavailable
        stratz_positions = await get_match_positions(match_id_int)
        if stratz_positions and account_id in stratz_positions:
            role = stratz_positions[account_id]
        else:
            try:
                role = detect_role(match_meta, account_id)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=f"Could not detect player role: {exc}")

    role_label = ROLE_LABELS.get(role, "carry")
    role_profile = get_role_profile(role)

    # Cache read — short-circuit if we have a fresh result for this match+player+role
    if not req.force_reanalyze and not req.force_redownload:
        cached = read_analysis_cache(match_id_int, account_id, role)
        if cached:
            return JSONResponse(content=cached)

    # 5. Download + parse replay
    # OpenDota only sets replay_url for parsed matches, but cluster + replay_salt
    # are always present — construct the URL directly as a fallback.
    replay_url = match_meta.get("replay_url")
    if not replay_url:
        cluster = match_meta.get("cluster")
        salt = match_meta.get("replay_salt")
        if cluster and salt:
            replay_url = f"http://replay{cluster}.valve.net/570/{match_id_int}_{salt}.dem.bz2"
        else:
            # OpenDota hasn't fetched this match from Valve yet — trigger a parse and wait
            try:
                replay_url = await request_parse_and_wait(match_id_int)
            except TimeoutError as exc:
                raise HTTPException(status_code=503, detail=str(exc))
            # Re-fetch match_meta — the parse populates lane_role, last_hits, gold_per_min, etc.
            # The original fetch happened before the parse completed so that data was absent.
            try:
                match_meta = await get_match(match_id_int)
            except Exception as exc:
                raise HTTPException(status_code=404, detail=f"Match re-fetch failed: {exc}")
    try:
        async with download_and_decompress(replay_url, match_id_int, force_redownload=req.force_redownload) as dem_path:
            records = parse_replay(dem_path)
    except ReplayExpiredError:
        # Replay expired — fall back to OpenDota-only metrics (degraded mode)
        from dota_coach.extractor import extract_metrics_from_opendota
        try:
            metrics = extract_metrics_from_opendota(account_id, match_meta)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"Degraded extraction failed: {exc}")
        degraded = True
        timeline = ""
    else:
        degraded = False
        # 6. Extract metrics
        try:
            metrics = extract_metrics(records, account_id, match_meta)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"Extraction failed: {exc}")

        # 7. Build timeline for chat context
        our_meta = next(
            (p for p in match_meta["players"] if p.get("account_id") == account_id),
            None,
        )
        our_npc = f"npc_dota_hero_{metrics.hero.lower()}" if our_meta else ""
        timeline = build_timeline(records, our_npc, metrics.hero)

    # 8. Enrich with benchmarks + patch data
    enrichment = await enrich(metrics, match_meta)

    # 9. Detect errors (v2: percentile-based, role-aware)
    errors = detect_errors(metrics, role_profile=role_profile, enrichment=enrichment)

    # 10. Build LLM prompt + call LLM
    system_prompt = build_system_prompt(role=role, turbo=metrics.turbo)
    user_message = build_user_message(metrics, errors, role=role, enrichment=enrichment)

    model = os.environ.get("LLM_MODEL", "anthropic/claude-sonnet-4-6")
    try:
        coaching_report = get_coaching(system_prompt, user_message, model)
    except CoachError as exc:
        coaching_report = f"LLM error: {exc}"

    # 11. Extract priority focus from LLM output
    priority_focus = ""
    for line in coaching_report.split("\n"):
        if "PRIORITY FOCUS" in line.upper():
            priority_focus = line.split(":", 1)[-1].strip() if ":" in line else line
            break

    # 12. Build response
    report = MatchReport(
        match_id=match_id_int,
        hero=metrics.hero,
        role=role,
        role_label=role_label,
        result=metrics.result,
        duration_minutes=metrics.duration_minutes,
        patch=enrichment.patch_name,
        turbo=metrics.turbo,
        degraded=degraded,
        metrics=metrics,
        benchmarks=enrichment.benchmarks,
        errors=errors,
        coaching_report=coaching_report,
        priority_focus=priority_focus,
        timeline=timeline,
    )

    report_dict = report.model_dump()
    write_analysis_cache(match_id_int, account_id, role, report_dict)
    return JSONResponse(content=report_dict)


# ---------------------------------------------------------------------------
# POST /chat
# ---------------------------------------------------------------------------

@app.post("/chat")
async def chat(request: ChatRequest):
    """Stream LLM responses for follow-up questions about a match."""
    from fastapi.responses import StreamingResponse

    from dota_coach.coach import stream_llm
    from dota_coach.prompt import build_chat_messages

    messages = build_chat_messages(request)
    model = os.environ.get("LLM_MODEL", "anthropic/claude-sonnet-4-6")

    async def event_stream():
        async for chunk in stream_llm(messages, model):
            yield f"data: {json.dumps(chunk)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# Root → serve index.html
# ---------------------------------------------------------------------------

@app.get("/")
async def root():
    """Serve the frontend."""
    from fastapi.responses import FileResponse

    index_path = _STATIC_DIR / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return {"message": "Dota 2 Personal Coach API v2. POST /analyze to start."}
