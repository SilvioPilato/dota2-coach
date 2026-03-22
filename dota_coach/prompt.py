"""Build LLM messages from MatchMetrics + DetectedErrors (v2: role-aware)."""
from __future__ import annotations

import warnings
from typing import Optional

from dota_coach import config
from dota_coach.models import ChatRequest, DetectedError, EnrichmentContext, HeroBenchmark, MatchMetrics
from dota_coach.role import ROLE_LABELS

_TOKEN_BUDGET = config.TOKEN_BUDGET

# ---------------------------------------------------------------------------
# Few-shot examples (one per role, ~80 tokens each)
# ---------------------------------------------------------------------------

_FEW_SHOT = {
    1: (
        "EXAMPLE (Anti-Mage, carry, loss, 38 min):\n"
        "LH@10: 32 | Deaths before 10: 3 (at 4.2, 7.1, 9.8) | GPM: 341 (12th pct)\n"
        "First core: Battle Fury at 22.4 min\n\n"
        "MISTAKE 1 (Critical): 3 deaths before 10 min including at 4:12.\n"
        "→ Fix: Glance minimap before each CS past T1. If mid missing >15s, hug safe side.\n\n"
        "MISTAKE 2 (High): Battle Fury at 22:24 is 4+ min late.\n"
        "→ Fix: Decline every fight until BF. Ping \"need farm\" and keep moving.\n\n"
        "MISTAKE 3 (High): 32 LH at 10 is below AM's ceiling.\n"
        "→ Fix: Pull at 1:20 and 3:20 to create slow push. CS under T1.\n\n"
        "PRIORITY FOCUS: Treat BF timing as a hard constraint."
    ),
    2: (
        "EXAMPLE (Storm Spirit, mid, loss, 32 min):\n"
        "LH@10: 38 | Deaths before 10: 2 (at 3.1, 8.5) | GPM: 368 (18th pct)\n"
        "First core: Orchid at 20.1 min\n\n"
        "MISTAKE 1 (Critical): Died at 3:10 mid — early solo death loses rune control.\n"
        "→ Fix: Play under tower until bottle. Only trade when you have mana advantage.\n\n"
        "MISTAKE 2 (High): Orchid at 20 min is too late for Storm's timing window.\n"
        "→ Fix: Farm jungle between waves after level 6. Stack and clear medium camp.\n\n"
        "MISTAKE 3 (Medium): Low teamfight participation at 35%.\n"
        "→ Fix: After Orchid, you ARE the tempo. Join every fight you can reach with Ball.\n\n"
        "PRIORITY FOCUS: Hit Orchid timing by 16 min."
    ),
    3: (
        "EXAMPLE (Tidehunter, offlaner, loss, 40 min):\n"
        "LH@10: 28 | Deaths before 10: 1 | Stacks: 0 | TF participation: 45%\n\n"
        "MISTAKE 1 (High): Zero stacks created — offlaner should stack ancients.\n"
        "→ Fix: Stack the nearby ancient camp at x:53 on your way to lane.\n\n"
        "MISTAKE 2 (Medium): 45% teamfight participation is low for initiator.\n"
        "→ Fix: After Blink, stay with team. Ravage wins fights.\n\n"
        "PRIORITY FOCUS: Stack camps and show up to fights."
    ),
    4: (
        "EXAMPLE (Earth Spirit, soft support, win, 35 min):\n"
        "Wards placed: 5 | Stacks: 1 | Hero healing: 2100 | TF participation: 60%\n\n"
        "MISTAKE 1 (High): Only 5 wards placed in 35 min — pos 4 should average 10+.\n"
        "→ Fix: Buy wards on cooldown. Place aggressively on enemy side of map.\n\n"
        "MISTAKE 2 (Medium): 1 stack in 35 min — easy value left on table.\n"
        "→ Fix: Stack a camp while rotating between lanes.\n\n"
        "PRIORITY FOCUS: Ward more — vision wins games."
    ),
    5: (
        "EXAMPLE (Crystal Maiden, hard support, loss, 38 min):\n"
        "Wards placed: 7 | Stacks: 0 | Hero healing: 1800 | TF participation: 70%\n\n"
        "MISTAKE 1 (High): 7 wards in 38 min is below average for pos 5.\n"
        "→ Fix: Place wards proactively before objectives. Don't save for \"perfect\" spots.\n\n"
        "MISTAKE 2 (Medium): Zero stacks hurts carry farm speed.\n"
        "→ Fix: Stack the safe lane large camp at x:53 on your way to pull.\n\n"
        "PRIORITY FOCUS: Ward placement is your #1 job."
    ),
}

# Turbo-specific few-shot example (one shared, turbo-calibrated values)
_FEW_SHOT_TURBO = (
    "EXAMPLE (Anti-Mage, carry, loss, 25 min, TURBO):\n"
    "LH@10: 55 | Deaths before 10: 2 (at 3.8, 8.2) | GPM: 620\n"
    "First core: Battle Fury at 11.3 min\n\n"
    "MISTAKE 1 (Critical): 2 deaths before 10 min in Turbo — respawn is fast but gold swing is huge.\n"
    "→ Fix: Play safe. In Turbo the farm will come — dying costs more than missing a wave.\n\n"
    "MISTAKE 2 (High): Battle Fury at 11:18 — good for ranked but slow for Turbo.\n"
    "→ Fix: Target sub-10 min BF in Turbo. Skip Perseverance, go Demon Edge first.\n\n"
    "PRIORITY FOCUS: Sub-10 min first core is the Turbo standard."
)


def build_system_prompt(role: int = 1, turbo: bool = False) -> str:
    """Build the coaching system prompt, injecting the role label and few-shot example."""
    role_label = ROLE_LABELS.get(role, "carry")

    if turbo:
        example = _FEW_SHOT_TURBO
        mode_note = (
            "\nThis is a TURBO match. Gold and XP are accelerated (~2x passive gold). "
            "Adjust expectations: GPM ~600-900, first core < 10 min, LH@10 ~60-80. "
            "Percentile benchmarks are NOT available for Turbo — use absolute metrics only.\n"
        )
    else:
        example = _FEW_SHOT.get(role, _FEW_SHOT[1])
        mode_note = ""

    return (
        f"You are a concise Dota 2 coach reviewing a {role_label} (position {role}) performance.\n"
        f"{mode_note}"
        "Analyze the metrics and percentile rankings provided. Identify the 3 most impactful\n"
        "mistakes, ranked by how much they cost the player. Be direct. Use Dota terminology.\n"
        "Give specific, actionable advice — not generic tips.\n\n"
        "Format your response exactly as:\n\n"
        "MISTAKE 1 (Critical/High/Medium): [what went wrong]\n"
        "→ Fix: [one concrete action for next game]\n\n"
        "MISTAKE 2 ...\n"
        "MISTAKE 3 ...\n\n"
        "PRIORITY FOCUS: [single most important habit to change]\n\n"
        f"{example}"
    )


def _benchmark_line(benchmarks: list[HeroBenchmark], metric: str, label: str, value: float) -> str:
    """Format a metric line with percentile info if benchmark available."""
    bench = next((b for b in benchmarks if b.metric == metric), None)
    if bench:
        return f"- {label}: {value:.0f} ({bench.player_pct:.0%} pct, global median {bench.bracket_avg:.0f})"
    return f"- {label}: {value:.0f}"


def build_user_message(
    metrics: MatchMetrics,
    errors: list[DetectedError],
    role: int = 1,
    enrichment: Optional[EnrichmentContext] = None,
) -> str:
    """Build the user message with role-specific metrics blocks."""
    role_label = ROLE_LABELS.get(role, "carry")
    # Skip benchmarks for turbo — percentiles are not meaningful
    benchmarks = enrichment.benchmarks if enrichment and not metrics.turbo else []
    lines: list[str] = []

    # Match header
    turbo_tag = " | TURBO" if metrics.turbo else ""
    lines.append(
        f"Match: {metrics.hero} | pos {role} ({role_label}) | {metrics.result.upper()} | "
        f"{metrics.duration_minutes:.0f} min | Match ID: {metrics.match_id}{turbo_tag}"
    )
    lines.append("")

    # --- Role-specific PERFORMANCE block ---
    lines.append("PERFORMANCE (percentiles are global, all brackets, this hero):")

    if role in (1, 2):
        # Pos 1/2: GPM, LH, first core, NW deltas
        lines.append(_benchmark_line(benchmarks, "gold_per_min", "GPM", metrics.gpm))
        lh_per_min = metrics.total_last_hits / metrics.duration_minutes if metrics.duration_minutes > 0 else 0
        lines.append(_benchmark_line(benchmarks, "last_hits_per_min", "LH/min (total game)", lh_per_min))
        lines.append(f"- LH at 10 min: {metrics.lh_at_10}")
        core_str = (
            f"{metrics.first_core_item_name} at {metrics.first_core_item_minute:.1f} min"
            if metrics.first_core_item_minute is not None
            else "None purchased"
        )
        lines.append(f"- First core: {core_str}")
        if metrics.enemy_carry_net_worth_at_10 > 0:
            delta_10 = metrics.net_worth_at_10 - metrics.enemy_carry_net_worth_at_10
            delta_20 = metrics.net_worth_at_20 - metrics.enemy_carry_net_worth_at_20
            lines.append(f"- Net worth delta at 10: {delta_10:+d}g vs opposing pos 1")
            lines.append(f"- Net worth delta at 20: {delta_20:+d}g vs opposing pos 1")

    elif role == 3:
        # Pos 3: GPM, LH, stacks, stun time
        lines.append(_benchmark_line(benchmarks, "gold_per_min", "GPM", metrics.gpm))
        lh_per_min = metrics.total_last_hits / metrics.duration_minutes if metrics.duration_minutes > 0 else 0
        lines.append(_benchmark_line(benchmarks, "last_hits_per_min", "LH/min (total game)", lh_per_min))
        lines.append(f"- LH at 10 min: {metrics.lh_at_10}")
        stacks = metrics.stacks_created if metrics.stacks_created is not None else 0
        lines.append(f"- Stacks created: {stacks}")

    elif role in (4, 5):
        # Pos 4/5: ward placements, deward_pct, stacks, hero healing
        wp = metrics.ward_placements if metrics.ward_placements is not None else 0
        lines.append(f"- Ward placements: {wp}")
        if metrics.deward_pct is not None:
            lines.append(f"- Deward rate: {metrics.deward_pct:.0%} of enemy wards removed")
        stacks = metrics.stacks_created if metrics.stacks_created is not None else 0
        lines.append(f"- Stacks created: {stacks}")
        healing = metrics.hero_healing if metrics.hero_healing is not None else 0
        lines.append(f"- Hero healing: {healing}")

    lines.append("")

    # --- TEAMFIGHTS block ---
    lines.append("TEAMFIGHTS:")
    if metrics.teamfight_participation_rate is not None:
        lines.append(f"- Participation: {metrics.teamfight_participation_rate:.0%}")
    else:
        lines.append("- Participation: N/A")
    death_detail = ""
    if metrics.deaths_before_10 > 0:
        timestamps = ", ".join(f"{t:.1f}" for t in metrics.death_timestamps_laning)
        death_detail = f" (at {timestamps} min)"
    lines.append(f"- Deaths before 10 min: {metrics.deaths_before_10}{death_detail}")
    lines.append("")

    # --- PATCH CONTEXT block (when enrichment available) ---
    if enrichment:
        patch_label = enrichment.patch_name or "unknown"
        lines.append(f"PATCH CONTEXT ({patch_label}):")
        for item_name, cost in enrichment.item_costs.items():
            display = item_name.replace("item_", "").replace("_", " ").title()
            lines.append(f"- {display}: {cost}g")
        if enrichment.hero_base_stats:
            stats = enrichment.hero_base_stats
            base_dmg = (stats.get("base_attack_min", 0) + stats.get("base_attack_max", 0)) / 2
            lines.append(f"- {metrics.hero}: base damage ~{base_dmg:.0f}, move speed {stats.get('move_speed', 0):.0f}")
        lines.append("")

    # --- DETECTED ISSUES ---
    top_errors = errors[:3]
    if top_errors:
        lines.append("DETECTED ISSUES (auto-flagged, top 3 by severity):")
        for e in top_errors:
            pct_info = f" ({e.player_pct:.0%} pct)" if e.player_pct is not None else ""
            lines.append(f"- [{e.severity.upper()}] {e.description} — {e.metric_value}{pct_info}")

    message = "\n".join(lines)

    # Token budget warning (approximate: 1 token ≈ 4 chars)
    estimated_tokens = len(build_system_prompt(role, turbo=metrics.turbo)) // 4 + len(message) // 4
    if estimated_tokens > _TOKEN_BUDGET:
        warnings.warn(
            f"Estimated prompt tokens ({estimated_tokens}) exceeds budget of {_TOKEN_BUDGET}.",
            stacklevel=2,
        )

    return message


# ---------------------------------------------------------------------------
# Chat message builder
# ---------------------------------------------------------------------------

_MAX_HISTORY = 20
_KEEP_TAIL = 10


def _build_chat_system_prompt(ctx: "MatchReport") -> str:
    """Assemble the system message content for a follow-up chat turn."""
    role_label = ROLE_LABELS.get(ctx.role, "carry")
    lines: list[str] = [
        f"You are a Dota 2 coach who has just reviewed a {role_label} match for this player.",
        "You have full access to the match data, detected mistakes, and a timeline of key events.",
        "Answer questions about this specific match or about Dota 2 strategy in general.",
        "Be direct and specific. Reference match data and timestamps when relevant.",
        "If asked about something not in the match data, answer from general Dota knowledge",
        "and clearly distinguish it from match-specific observations.",
        "",
    ]

    # Metrics block
    m = ctx.metrics
    lines.append("MATCH METRICS:")
    lines.append(f"Hero: {m.hero} | Role: pos {ctx.role} ({role_label}) | Result: {m.result.upper()} | Duration: {m.duration_minutes:.0f} min")
    lines.append(f"GPM: {m.gpm} | XPM: {m.xpm} | LH@10: {m.lh_at_10} | Denies@10: {m.denies_at_10}")
    lines.append(f"Deaths before 10: {m.deaths_before_10}")
    lines.append("")

    # Errors block
    if ctx.errors:
        lines.append("DETECTED ERRORS:")
        for e in ctx.errors:
            pct_info = f" ({e.player_pct:.0%} pct)" if e.player_pct is not None else ""
            lines.append(f"- [{e.severity.upper()}] {e.description} — {e.metric_value}{pct_info}")
        lines.append("")

    # Patch context
    if ctx.patch:
        lines.append(f"PATCH: {ctx.patch}")
        lines.append("")

    # Timeline
    if ctx.timeline:
        lines.append("MATCH TIMELINE:")
        lines.append(ctx.timeline)
    else:
        lines.append("Timeline not available.")

    return "\n".join(lines)


def build_chat_messages(request: ChatRequest) -> list[dict]:
    """Assemble message dicts for ``litellm.completion()`` from a *ChatRequest*.

    Returns a list of ``{"role": ..., "content": ...}`` dicts:
      0  – system prompt (coach persona + match data)
      1  – assistant turn (initial coaching report)
      *  – conversation history (truncated if needed)
      -1 – current user message
    """
    ctx = request.match_context

    system_msg: dict = {"role": "system", "content": _build_chat_system_prompt(ctx)}
    assistant_msg: dict = {"role": "assistant", "content": ctx.coaching_report}

    history_msgs = [{"role": h.role, "content": h.content} for h in request.history]

    # Truncate history if it exceeds the cap
    if len(history_msgs) > _MAX_HISTORY:
        history_msgs = history_msgs[-_KEEP_TAIL:]

    user_msg: dict = {"role": "user", "content": request.user_message}

    return [system_msg, assistant_msg, *history_msgs, user_msg]
