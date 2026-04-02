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
# MatchSummary model + hero lookup (used by GET /recent-matches)
# ---------------------------------------------------------------------------

class MatchSummary(BaseModel):
    match_id: int
    hero_id: int
    hero_name: str           # e.g. "Anti-Mage" or "" if unknown
    start_time: int          # Unix timestamp
    duration_seconds: int
    won: bool                # True if player won
    kills: int
    deaths: int
    assists: int
    analyzed: bool           # True if in our local history DB
    replay_available: bool   # True if start_time is within 7 days
    game_mode: int = 0       # OpenDota game_mode ID (23 = Turbo, 1 = All Pick, etc.)
    lobby_type: int = 0      # OpenDota lobby_type ID (7 = Ranked, 0 = Unranked, etc.)


_HERO_NAMES: dict[int, str] = {
    1: "Anti-Mage", 2: "Axe", 3: "Bane", 4: "Bloodseeker", 5: "Crystal Maiden",
    6: "Drow Ranger", 7: "Earthshaker", 8: "Juggernaut", 9: "Mirana", 10: "Morphling",
    11: "Shadow Fiend", 12: "Phantom Lancer", 13: "Puck", 14: "Pudge", 15: "Razor",
    16: "Sand King", 17: "Storm Spirit", 18: "Sven", 19: "Tiny", 20: "Vengeful Spirit",
    21: "Windranger", 22: "Zeus", 23: "Kunkka", 25: "Lina", 26: "Lion",
    27: "Shadow Shaman", 28: "Slardar", 29: "Tidehunter", 30: "Witch Doctor", 31: "Lich",
    32: "Riki", 33: "Enigma", 34: "Tinker", 35: "Sniper", 36: "Necrophos",
    37: "Warlock", 38: "Beastmaster", 39: "Queen of Pain", 40: "Venomancer",
    41: "Faceless Void", 42: "Wraith King", 43: "Death Prophet", 44: "Phantom Assassin",
    45: "Pugna", 46: "Templar Assassin", 47: "Viper", 48: "Luna", 49: "Dragon Knight",
    50: "Dazzle", 51: "Clockwerk", 52: "Leshrac", 53: "Nature's Prophet",
    54: "Lifestealer", 55: "Dark Seer", 56: "Clinkz", 57: "Omniknight",
    58: "Enchantress", 59: "Huskar", 60: "Night Stalker", 61: "Broodmother",
    62: "Bounty Hunter", 63: "Weaver", 64: "Jakiro", 65: "Batrider", 66: "Chen",
    67: "Spectre", 68: "Ancient Apparition", 69: "Doom", 70: "Ursa",
    71: "Spirit Breaker", 72: "Gyrocopter", 73: "Alchemist", 74: "Invoker",
    75: "Silencer", 76: "Outworld Devourer", 77: "Lycan", 78: "Brewmaster",
    79: "Shadow Demon", 80: "Lone Druid", 81: "Chaos Knight", 82: "Meepo",
    83: "Treant Protector", 84: "Ogre Magi", 85: "Undying", 86: "Rubick",
    87: "Disruptor", 88: "Nyx Assassin", 89: "Naga Siren", 90: "Keeper of the Light",
    91: "Io", 92: "Visage", 93: "Slark", 94: "Medusa", 95: "Troll Warlord",
    96: "Centaur Warrunner", 97: "Magnus", 98: "Timbersaw", 99: "Bristleback",
    100: "Tusk", 101: "Skywrath Mage", 102: "Abaddon", 103: "Elder Titan",
    104: "Legion Commander", 105: "Techies", 106: "Ember Spirit", 107: "Earth Spirit",
    108: "Underlord", 109: "Terrorblade", 110: "Phoenix", 111: "Oracle",
    112: "Winter Wyvern", 113: "Arc Warden", 114: "Monkey King",
    119: "Dark Willow", 120: "Pangolier", 121: "Grimstroke", 123: "Hoodwink",
    126: "Void Spirit", 128: "Snapfire", 129: "Mars", 131: "Ring Master",
    135: "Dawnbreaker", 136: "Marci", 137: "Primal Beast", 138: "Muerta",
    145: "Kez", 155: "Largo",
}


# ---------------------------------------------------------------------------
# POST /analyze
# ---------------------------------------------------------------------------

@app.post("/analyze")
async def analyze(req: AnalyzeRequest):
    """Run the full analysis pipeline, streaming phase progress via SSE."""
    from sse_starlette.sse import EventSourceResponse

    async def _stream():
        import time as _time

        from dota_coach.cache import read_analysis_cache, write_analysis_cache
        from dota_coach.coach import CoachError, get_coaching
        from dota_coach.detector import detect_errors
        from dota_coach.downloader import ReplayExpiredError, download_and_decompress
        from dota_coach.enricher import enrich, get_core_items
        from dota_coach.extractor import build_timeline, extract_metrics
        from dota_coach.models import MatchReport
        from dota_coach.opendota import get_match, request_parse_and_wait
        from dota_coach.parser import ParserNotRunningError, check_sidecar_health, parse_replay
        from dota_coach.prompt import build_system_prompt, build_user_message
        from dota_coach.role import ROLE_LABELS, detect_role, get_role_profile

        t0 = _time.monotonic()
        state = {"phase_start": t0}

        def elapsed_total_ms():
            return int((_time.monotonic() - t0) * 1000)

        def elapsed_phase_ms():
            return int((_time.monotonic() - state["phase_start"]) * 1000)

        def start_phase():
            state["phase_start"] = _time.monotonic()

        def step(phase, status, **kw):
            data = {"phase": phase, "status": status, "elapsed_ms": elapsed_total_ms()}
            data.update(kw)
            return {"event": "step", "data": json.dumps(data)}

        # ------------------------------------------------------------------ #
        # Phase: metadata                                                       #
        # ------------------------------------------------------------------ #
        start_phase()
        yield step("metadata", "running", detail="Fetching match data")

        try:
            check_sidecar_health()
        except ParserNotRunningError as exc:
            yield step("metadata", "error", message=str(exc))
            return

        try:
            match_id_int = int(req.match_id)
        except ValueError:
            yield step("metadata", "error", message=f"Invalid match_id: {req.match_id}")
            return

        try:
            match_meta = await get_match(match_id_int)
        except Exception as exc:
            yield step("metadata", "error", message=f"Match not found: {exc}")
            return

        if req.player_id:
            try:
                raw_id = int(req.player_id)
            except ValueError:
                yield step("metadata", "error", message=f"Invalid player_id: {req.player_id}")
                return
            _STEAM64_BASE = 76561197960265728
            account_id = raw_id - _STEAM64_BASE if raw_id > _STEAM64_BASE else raw_id
        else:
            players = match_meta.get("players", [])
            if not players:
                yield step("metadata", "error", message="No players in match data")
                return
            account_id = players[0].get("account_id", 0)

        if req.role_override and 1 <= req.role_override <= 5:
            role = req.role_override
        else:
            from dota_coach.stratz import get_match_positions
            stratz_positions = await get_match_positions(match_id_int)
            if stratz_positions and account_id in stratz_positions:
                role = stratz_positions[account_id]
            else:
                try:
                    role = detect_role(match_meta, account_id)
                except ValueError as exc:
                    yield step("metadata", "error", message=f"Could not detect role: {exc}")
                    return

        role_label = ROLE_LABELS.get(role, "carry")
        role_profile = get_role_profile(role)

        # Cache short-circuit — emit all phases as done and stream the cached report
        if not req.force_reanalyze and not req.force_redownload:
            cached = read_analysis_cache(match_id_int, account_id, role)
            if cached:
                yield step("metadata", "done", detail="Loaded from cache", phase_ms=elapsed_phase_ms())
                yield {"event": "report", "data": json.dumps(cached)}
                return

        yield step("metadata", "done", phase_ms=elapsed_phase_ms())

        # ------------------------------------------------------------------ #
        # Phase: download                                                       #
        # ------------------------------------------------------------------ #
        start_phase()
        yield step("download", "running", detail="Downloading replay")

        replay_url = match_meta.get("replay_url")
        if not replay_url:
            cluster = match_meta.get("cluster")
            salt = match_meta.get("replay_salt")
            if cluster and salt:
                replay_url = f"http://replay{cluster}.valve.net/570/{match_id_int}_{salt}.dem.bz2"
            else:
                yield step("download", "running", detail="Requesting OpenDota parse…")
                try:
                    replay_url = await request_parse_and_wait(match_id_int)
                except TimeoutError as exc:
                    yield step("download", "error", message=str(exc))
                    return
                try:
                    match_meta = await get_match(match_id_int)
                except Exception as exc:
                    yield step("download", "error", message=f"Match re-fetch failed: {exc}")
                    return

        # ------------------------------------------------------------------ #
        # Phase: parse (runs inside the download context manager)              #
        # ------------------------------------------------------------------ #
        degraded = False
        timeline = ""
        records = None

        try:
            async with download_and_decompress(replay_url, match_id_int, force_redownload=req.force_redownload) as dem_path:
                yield step("download", "done", phase_ms=elapsed_phase_ms())

                start_phase()
                yield step("parse", "running", detail="Parsing replay")
                try:
                    records = parse_replay(dem_path)
                except Exception as exc:
                    yield step("parse", "error", message=f"Parse failed: {exc}")
                    return
                yield step("parse", "done", phase_ms=elapsed_phase_ms())

        except ReplayExpiredError:
            yield step("download", "done", detail="Replay expired — using OpenDota data", phase_ms=elapsed_phase_ms())
            yield step("parse", "done", detail="Skipped (replay unavailable)", phase_ms=0)
            degraded = True
        except Exception as exc:
            yield step("download", "error", message=f"Download failed: {exc}")
            return

        # ------------------------------------------------------------------ #
        # Phase: enrich                                                         #
        # ------------------------------------------------------------------ #
        start_phase()
        yield step("enrich", "running", detail="Enriching with benchmarks")

        core_items = await get_core_items()

        if degraded:
            from dota_coach.extractor import extract_metrics_from_opendota
            try:
                metrics = extract_metrics_from_opendota(account_id, match_meta)
            except ValueError as exc:
                yield step("enrich", "error", message=f"Extraction failed: {exc}")
                return
        else:
            try:
                metrics = extract_metrics(records, account_id, match_meta, core_items=core_items)
            except ValueError as exc:
                yield step("enrich", "error", message=f"Extraction failed: {exc}")
                return

            our_meta = next(
                (p for p in match_meta["players"] if p.get("account_id") == account_id),
                None,
            )
            our_npc = f"npc_dota_hero_{metrics.hero.lower()}" if our_meta else ""
            timeline = build_timeline(records, our_npc, metrics.hero)

        from dota_coach.stratz import rank_tier_to_stratz_bracket
        _our_player_meta = next(
            (p for p in match_meta.get("players", []) if p.get("account_id") == account_id),
            None,
        )
        _rank_tier: int = (_our_player_meta or {}).get("rank_tier") or 0
        _bracket = rank_tier_to_stratz_bracket(_rank_tier) if _rank_tier else "LEGEND_ANCIENT"

        enrichment = await enrich(metrics, match_meta, account_id=account_id)

        # Enrich lane matchup data: enemy WRs + ally synergy (mutates metrics in place)
        from dota_coach.enricher import enrich_lane_matchup, _get_heroes_data
        _heroes_data = await _get_heroes_data()
        await enrich_lane_matchup(metrics, _bracket, _heroes_data, match_meta=match_meta, our_account_id=account_id)

        errors = detect_errors(metrics, role_profile=role_profile, enrichment=enrichment)
        yield step("enrich", "done", phase_ms=elapsed_phase_ms())

        # ------------------------------------------------------------------ #
        # Phase: llm                                                           #
        # ------------------------------------------------------------------ #
        start_phase()
        yield step("llm", "running", detail="Generating coaching report")

        system_prompt = build_system_prompt(role=role, turbo=metrics.turbo)
        user_message = build_user_message(metrics, errors, role=role, enrichment=enrichment)

        model = os.environ.get("LLM_MODEL", "anthropic/claude-sonnet-4-6")
        try:
            coaching_report = get_coaching(system_prompt, user_message, model)
        except CoachError as exc:
            coaching_report = f"LLM error: {exc}"

        yield step("llm", "done", phase_ms=elapsed_phase_ms())

        # ------------------------------------------------------------------ #
        # Build + emit report                                                  #
        # ------------------------------------------------------------------ #
        priority_focus = ""
        for line in coaching_report.split("\n"):
            if "PRIORITY FOCUS" in line.upper():
                priority_focus = line.split(":", 1)[-1].strip() if ":" in line else line
                break

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
            local_benchmarks=enrichment.local_benchmarks,
            local_benchmark_progress=enrichment.local_benchmark_progress,
        )

        report_dict = report.model_dump()
        write_analysis_cache(match_id_int, account_id, role, report_dict)

        from dota_coach.history import save_match_report
        save_match_report(match_id_int, account_id, role, report_dict)

        yield {"event": "report", "data": json.dumps(report_dict)}

    return EventSourceResponse(_stream())


# ---------------------------------------------------------------------------
# GET /history/{account_id}
# ---------------------------------------------------------------------------

@app.get("/history/{account_id}")
async def match_history(account_id: int, limit: int = 20):
    """Return stored match history for an account (newest first)."""
    from dota_coach.history import get_match_history
    records = get_match_history(account_id, limit=limit)
    return JSONResponse(content=records)


@app.get("/report/{account_id}/{match_id}")
async def get_report(account_id: int, match_id: int):
    """Return the stored MatchReport for a specific match (any role)."""
    from dota_coach.history import get_match_history

    _STEAM64_BASE = 76561197960265728
    if account_id > _STEAM64_BASE:
        account_id = account_id - _STEAM64_BASE

    # get_match_history returns newest-first; find the one matching match_id
    all_reports = get_match_history(account_id, limit=100)
    for r in all_reports:
        if r.get("match_id") == match_id:
            return JSONResponse(content=r)
    raise HTTPException(status_code=404, detail="Report not found")


# ---------------------------------------------------------------------------
# GET /recent-matches/{account_id}
# ---------------------------------------------------------------------------

@app.get("/recent-matches/{account_id}")
async def recent_matches(account_id: int, offset: int = 0):
    """Return OpenDota recent matches joined with local analysis status."""
    from datetime import datetime, timezone
    from dota_coach.history import get_analyzed_ids
    from dota_coach.opendota import get_paginated_matches

    # Normalize Steam 64-bit ID → OpenDota 32-bit account ID
    _STEAM64_BASE = 76561197960265728
    if account_id > _STEAM64_BASE:
        account_id = account_id - _STEAM64_BASE

    try:
        raw = await get_paginated_matches(account_id, limit=20, offset=offset)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"OpenDota error: {exc}")

    analyzed_ids = get_analyzed_ids(account_id)
    now_ts = int(datetime.now(timezone.utc).timestamp())
    seven_days = 7 * 24 * 3600

    summaries = []
    for m in raw:
        match_id = m.get("match_id", 0)
        start_time = m.get("start_time", 0)
        player_slot = m.get("player_slot", 0)
        radiant_win = m.get("radiant_win", False)
        is_radiant = player_slot < 128
        won = (is_radiant and radiant_win) or (not is_radiant and not radiant_win)
        hero_id = m.get("hero_id", 0)
        summaries.append(MatchSummary(
            match_id=match_id,
            hero_id=hero_id,
            hero_name=_HERO_NAMES.get(hero_id, ""),
            start_time=start_time,
            duration_seconds=m.get("duration", 0),
            won=won,
            kills=m.get("kills", 0),
            deaths=m.get("deaths", 0),
            assists=m.get("assists", 0),
            analyzed=match_id in analyzed_ids,
            replay_available=(now_ts - start_time) < seven_days,
            game_mode=m.get("game_mode", 0),
            lobby_type=m.get("lobby_type", 0),
        ))

    return JSONResponse(content=[s.model_dump() for s in summaries])


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
