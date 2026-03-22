"""Build LLM messages from MatchMetrics + DetectedErrors."""
from __future__ import annotations

import warnings

from dota_coach.models import DetectedError, MatchMetrics

_TOKEN_BUDGET = 800


def build_system_prompt() -> str:
    return (
        "You are a concise Dota 2 carry coach reviewing match data for a Crusader/Archon player.\n"
        "Your job is to identify the 3 most impactful mistakes from the metrics provided, ranked by how much they cost the player.\n"
        "Be direct. Use Dota terminology. Give specific, actionable advice — not generic tips.\n"
        "Format your response exactly as:\n\n"
        "MISTAKE 1 (Critical/High/Medium): [what went wrong]\n"
        "→ Fix: [one concrete action for next game]\n\n"
        "MISTAKE 2 ...\n"
        "MISTAKE 3 ...\n\n"
        "PRIORITY FOCUS: [single most important habit to change]"
    )


def build_user_message(metrics: MatchMetrics, errors: list[DetectedError]) -> str:
    lines: list[str] = []

    # Match header
    lines.append(
        f"Match: {metrics.hero} | {metrics.result.upper()} | "
        f"{metrics.duration_minutes:.0f} min | Match ID: {metrics.match_id}"
    )
    lines.append("")

    # Laning
    delta_10 = metrics.net_worth_at_10 - metrics.enemy_carry_net_worth_at_10
    death_detail = ""
    if metrics.deaths_before_10 > 0:
        timestamps = ", ".join(f"{t:.1f}" for t in metrics.death_timestamps_laning)
        death_detail = f" (at {timestamps} min)"

    lines.append("LANING (0–10 min):")
    lines.append(f"- Last hits at 10 min: {metrics.lh_at_10} (target: >=45)")
    lines.append(f"- Denies at 10 min: {metrics.denies_at_10}")
    lines.append(f"- Deaths before 10 min: {metrics.deaths_before_10}{death_detail}")
    lines.append(
        f"- Net worth at 10 min: {metrics.net_worth_at_10}g "
        f"(enemy carry: {metrics.enemy_carry_net_worth_at_10}g, delta: {delta_10:+d}g)"
    )
    lines.append("")

    # Farming
    delta_20 = metrics.net_worth_at_20 - metrics.enemy_carry_net_worth_at_20
    core_str = (
        f"{metrics.first_core_item_name} at {metrics.first_core_item_minute:.1f} min (target: <18 min)"
        if metrics.first_core_item_minute is not None
        else "None purchased"
    )

    lines.append("FARMING:")
    lines.append(f"- GPM: {metrics.gpm} | XPM: {metrics.xpm}")
    lines.append(
        f"- Net worth at 20 min: {metrics.net_worth_at_20}g "
        f"(enemy carry: {metrics.enemy_carry_net_worth_at_20}g, delta: {delta_20:+d}g)"
    )
    lines.append(f"- First core item: {core_str}")
    lines.append("")

    # Positioning & Habits
    lines.append("POSITIONING & HABITS:")
    lines.append(f"- Laning phase own-half positioning: {metrics.laning_heatmap_own_half_pct:.0%}")
    lines.append(f"- Ward purchases by carry: {metrics.ward_purchases}")
    lines.append("")

    # Teamfights (omit section if no data)
    if metrics.teamfight_participation_rate is not None:
        lines.append("TEAMFIGHTS:")
        lines.append(f"- Participation rate: {metrics.teamfight_participation_rate:.0%} (target: >=40%)")
        if metrics.teamfight_avg_damage_contribution is not None:
            lines.append(f"- Avg damage contribution: {metrics.teamfight_avg_damage_contribution:.0%}")
        lines.append("")

    # Detected issues (top 3)
    top_errors = errors[:3]
    if top_errors:
        lines.append("DETECTED ISSUES (auto-flagged):")
        for e in top_errors:
            lines.append(f"- [{e.severity.upper()}] {e.description} — {e.metric_value}")

    message = "\n".join(lines)

    # Token budget warning (approximate: 1 token ≈ 4 chars)
    estimated_tokens = len(build_system_prompt()) // 4 + len(message) // 4
    if estimated_tokens > _TOKEN_BUDGET:
        warnings.warn(
            f"Estimated prompt tokens ({estimated_tokens}) exceeds budget of {_TOKEN_BUDGET}.",
            stacklevel=2,
        )

    return message
