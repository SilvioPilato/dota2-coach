"""Typer CLI entry point."""
from __future__ import annotations

import asyncio
import os
import sys

from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from dota_coach import coach, detector, downloader, extractor, opendota, parser, prompt

app = typer.Typer(help="Dota 2 personal carry coach")
console = Console()

_SEVERITY_COLORS = {"critical": "red", "high": "yellow", "medium": "blue"}

DEFAULT_MODEL = "anthropic/claude-sonnet-4-6"


def _get_model(model_flag: Optional[str]) -> str:
    if model_flag:
        return model_flag
    return os.environ.get("LLM_MODEL", DEFAULT_MODEL)


@app.command()
def analyze(
    match: Optional[int] = typer.Option(None, "--match", help="Match ID to analyze"),
    player: Optional[str] = typer.Option(None, "--player", help="Steam ID (64-bit) or OpenDota account ID"),
    model: Optional[str] = typer.Option(None, "--model", help="LLM model (e.g. anthropic/claude-sonnet-4-6)"),
    local_replay: Optional[str] = typer.Option(None, "--local-replay", help="Path to local .dem file (skips CDN download)"),
) -> None:
    """Analyze a Dota 2 match and get carry coaching feedback."""
    if match is None and player is None:
        console.print("[red]Error:[/red] Provide --match <match_id> or --player <steam_id>")
        raise typer.Exit(1)

    asyncio.run(_run_analyze(match, player, model, local_replay))


@app.command()
def recent(
    player: str = typer.Argument(..., help="Steam ID (64-bit) or OpenDota account ID"),
) -> None:
    """Show the last 10 matches for a player."""
    asyncio.run(_run_recent(player))


async def _run_recent(player_str: str) -> None:
    account_id = _resolve_account_id(player_str)
    matches = await opendota.get_recent_matches(account_id, limit=10)
    console.print(Panel("[bold]Recent Matches[/bold]", style="cyan"))
    for m in matches:
        hero = m.get("hero_id", "?")
        is_radiant = m.get("player_slot", 0) < 128
        result = "WIN" if (m.get("radiant_win") and is_radiant) or (not m.get("radiant_win") and not is_radiant) else "LOSS"
        duration = m.get("duration", 0) // 60
        console.print(f"  [cyan]{m['match_id']}[/cyan]  hero={hero}  {result}  {duration}min")


async def _run_analyze(match_id: Optional[int], player_str: Optional[str], model_flag: Optional[str], local_replay: Optional[str] = None) -> None:
    model = _get_model(model_flag)
    account_id: Optional[int] = None

    if player_str:
        account_id = _resolve_account_id(player_str)
        if match_id is None:
            console.print(f"Fetching recent matches for account {account_id}...")
            matches = await opendota.get_recent_matches(account_id, limit=1)
            if not matches:
                console.print("[red]No recent matches found.[/red]")
                raise typer.Exit(1)
            match_id = matches[0]["match_id"]
            console.print(f"Using most recent match: [cyan]{match_id}[/cyan]")

    console.print(f"Fetching match [cyan]{match_id}[/cyan] from OpenDota...")
    match_meta = await opendota.get_match(match_id)

    if account_id is None:
        account_id = _pick_player(match_meta, console)
        if account_id is None:
            raise typer.Exit(1)

    replay_url = match_meta.get("replay_url")

    # Check sidecar before downloading
    console.print("Checking odota parser sidecar...")
    try:
        parser.check_sidecar_health()
    except parser.ParserNotRunningError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    if local_replay:
        from pathlib import Path
        dem_path = Path(local_replay)
        if not dem_path.exists():
            console.print(f"[red]Error:[/red] Local replay file not found: {local_replay}")
            raise typer.Exit(1)
        console.print(f"Using local replay: [cyan]{dem_path}[/cyan]")
        console.print("Parsing replay (this may take 30-60s)...")
        records = parser.parse_replay(dem_path)
    else:
        console.print("Downloading and decompressing replay...")
        try:
            async with downloader.download_and_decompress(replay_url) as dem_path:
                console.print("Parsing replay (this may take 30-60s)...")
                records = parser.parse_replay(dem_path)
        except downloader.ReplayExpiredError as e:
            console.print(f"[red]Error:[/red] {e}")
            raise typer.Exit(1)

    console.print("Extracting metrics...")
    metrics = extractor.extract_metrics(records, account_id, match_meta)

    errors = detector.detect_errors(metrics)

    system = prompt.build_system_prompt()
    user_msg = prompt.build_user_message(metrics, errors)

    console.print(f"Calling LLM ([cyan]{model}[/cyan])...")
    try:
        coaching_text = coach.get_coaching(system, user_msg, model)
    except coach.CoachError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    # --- Rich output ---
    # Match summary panel
    result_color = "green" if metrics.result == "win" else "red"
    summary = (
        f"Hero: [bold]{metrics.hero}[/bold]  |  "
        f"Result: [{result_color}]{metrics.result.upper()}[/{result_color}]  |  "
        f"Duration: {metrics.duration_minutes:.0f} min\n"
        f"GPM: {metrics.gpm}  |  XPM: {metrics.xpm}  |  "
        f"First core: {metrics.first_core_item_name or 'None'} "
        f"@ {f'{metrics.first_core_item_minute:.1f} min' if metrics.first_core_item_minute else 'N/A'}"
    )
    console.print(Panel(Text.from_markup(summary), title="Match Summary", border_style="cyan"))

    # Issues panel
    if errors:
        issues_lines = []
        for e in errors[:3]:
            color = _SEVERITY_COLORS.get(e.severity, "white")
            issues_lines.append(f"[{color}][{e.severity.upper()}][/{color}] {e.description} — {e.metric_value}")
        console.print(Panel("\n".join(issues_lines), title="Detected Issues", border_style="yellow"))

    # Coaching panel
    console.print(Panel(coaching_text, title="Coaching Report", border_style="green"))


def _pick_player(match_meta: dict, console: Console) -> int | None:
    """Display the 10 players and ask the user to identify themselves."""
    players = match_meta.get("players", [])
    console.print("\nWho are you in this match?")
    for i, p in enumerate(players, start=1):
        team = "[green]Radiant[/green]" if p.get("isRadiant") else "[red]Dire   [/red]"
        name = p.get("personaname") or "Anonymous"
        hero_id = p.get("hero_id", "?")
        console.print(f"  {i:2}. {team}  hero_id={hero_id:<4}  {name}")

    try:
        idx = typer.prompt("\nEnter player number (1–10)", type=int)
    except (typer.Abort, KeyboardInterrupt):
        return None

    if not 1 <= idx <= len(players):
        console.print(f"[red]Error:[/red] Invalid selection: {idx}")
        return None

    chosen = players[idx - 1]
    account_id = chosen.get("account_id")
    if not account_id:
        console.print("[red]Error:[/red] Selected player has no account ID (anonymous profile). Use --player with your Steam ID instead.")
        return None

    return account_id


def _resolve_account_id(player_str: str) -> int:
    """Accept either a 64-bit Steam ID or a 32-bit account ID string."""
    try:
        value = int(player_str)
    except ValueError:
        console.print(f"[red]Error:[/red] Invalid player ID: {player_str}")
        sys.exit(1)

    if value > 76561197960265728:
        return opendota.extract_account_id(value)
    return value


def main() -> None:
    app()


if __name__ == "__main__":
    main()
