# Dota 2 Personal Coach

AI-powered coaching tool that analyzes Dota 2 replays and delivers role-aware, percentile-based feedback for all 5 positions. Available as a CLI and a web UI.

## Features

- **All 5 roles** — auto-detected via [Stratz GraphQL API](https://stratz.com/api) (accurate pos 1–5), falling back to OpenDota GPM-ranking heuristic when no key is set
- **Percentile-based error detection** — severity from global OpenDota benchmarks (critical < 20th, high < 35th, medium < 45th)
- **Role-aware metrics** — each position observes different KPIs (GPM/LH for cores, wards/stacks/healing for supports)
- **Turbo mode** — detected automatically (`game_mode == 23`), uses absolute metrics with turbo-calibrated reference values
- **Patch context** — current item costs and hero base stats injected into prompts via `dotaconstants`
- **Replay cache** — decompressed `.dem` files cached to `~/.dota_coach/cache/` (7-day TTL); repeat analyses skip the ~100–500 MB download
- **Analysis cache** — full coaching results cached by `match_id + account_id + role`; identical requests return instantly without re-running the LLM
- **Browser persistence** — last result stored in `localStorage`; re-opening the page and re-submitting renders instantly without a server round-trip
- **Follow-up chat** — ask the coach questions about your match via streaming SSE
- **Web UI** — dark-themed single-page app with metric cards, benchmark bars, coaching report, chat panel, and Re-analyze / Re-download replay buttons
- **CLI** — terminal interface with Rich formatting

## Architecture

```
Browser / CLI
    │
    ▼
FastAPI backend (api.py)
    │
    ├─► OpenDota API        → match metadata + replay URL
    ├─► Stratz GraphQL API  → accurate pos 1–5 role (optional)
    ├─► Analysis Cache      → short-circuit on cache hit (match+player+role)
    ├─► Valve CDN           → .dem.bz2 replay file (or .dem disk cache)
    ├─► odota parser        → NDJSON event log (localhost:5600)
    ├─► Extractor           → MatchMetrics (typed Pydantic model)
    ├─► Role Detector       → RoleProfile (pos 1–5)
    ├─► Enricher            → benchmarks + patch data (disk-cached)
    ├─► Error Detector      → top 3 errors by severity
    ├─► Prompt Builder      → role-aware system + user message
    └─► LiteLLM             → coaching report from any LLM provider
```

## Prerequisites

- **Python ≥ 3.12**
- **odota parser** — the Go-based replay parser running as an HTTP sidecar on `localhost:5600`. See [odota/parser](https://github.com/odota/parser) for setup.
- **LLM API key** — any provider supported by [LiteLLM](https://docs.litellm.ai/) (e.g. `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`)

## Installation

```bash
pip install -e ".[dev]"
```

## Configuration

Create a `.env` file in the project root (or set these as environment variables):

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_MODEL` | `anthropic/claude-sonnet-4-6` | LiteLLM model identifier |
| `ANTHROPIC_API_KEY` | — | API key (or equivalent for your provider) |
| `STRATZ_API_KEY` | — | Optional. Enables accurate pos 1–5 role detection via [Stratz](https://stratz.com/api). Falls back to GPM-ranking heuristic if unset. |
| `DOTA_COACH_TOKEN_BUDGET` | `800` | Max tokens for LLM user message |

## Usage

### Web UI

```bash
uvicorn dota_coach.api:app --reload
```

Open [http://localhost:8000](http://localhost:8000), enter a Match ID and optionally a Player ID, then click **Analyze**.

After the coaching report loads:
- Use the **chat panel** below to ask follow-up questions
- Click **Re-analyze** to bypass the analysis cache and re-run the full pipeline (keeps the cached `.dem`)
- Click **Re-download replay** to delete the cached `.dem` and re-download from Valve CDN (implies re-analysis)

Results are stored in `localStorage` — re-submitting the same match+player pair renders instantly from the browser cache.

### CLI

```bash
# Analyze a match
dota-coach analyze --match 7823456789 --player 12345678

# Use a specific model
dota-coach analyze --match 7823456789 --model openai/gpt-4o

# Analyze a local replay file (skips download)
dota-coach analyze --match 7823456789 --local-replay ./replay.dem

# Show recent matches for a player
dota-coach recent 12345678
```

### API Endpoints

#### `POST /analyze`

Full analysis pipeline — returns a `MatchReport` JSON.

```json
{
  "match_id": "7823456789",
  "player_id": "12345678",
  "role_override": 1,
  "force_reanalyze": false,
  "force_redownload": false
}
```

- `force_reanalyze` — skip analysis cache; re-run full pipeline (keeps cached `.dem`)
- `force_redownload` — delete cached `.dem` and re-download; implies `force_reanalyze`

#### `POST /chat`

Streaming follow-up questions (Server-Sent Events).

```json
{
  "match_context": { "...MatchReport..." },
  "history": [
    { "role": "user", "content": "Why did I lose lane?" },
    { "role": "assistant", "content": "..." }
  ],
  "user_message": "Should I have bought BKB earlier?"
}
```

## Project Structure

```
dota_coach/
├── api.py          # FastAPI backend — POST /analyze, POST /chat, static serving
├── cache.py        # Disk cache helpers — analysis JSON + .dem file (7-day TTL)
├── cli.py          # Typer CLI — analyze, recent commands
├── coach.py        # LiteLLM calls — get_coaching(), stream_llm()
├── config.py       # Environment-based configuration
├── detector.py     # Error detection — percentile-based + absolute rules
├── downloader.py   # Replay download from Valve CDN with persistent .dem cache
├── enricher.py     # Benchmark + patch data fetcher with disk cache
├── extractor.py    # Parser NDJSON → MatchMetrics, build_timeline()
├── models.py       # Pydantic models — MatchMetrics, MatchReport, ChatRequest, etc.
├── opendota.py     # OpenDota API client — get_match(), get_benchmarks()
├── parser.py       # odota parser sidecar client
├── prompt.py       # LLM prompt builder — role-aware, turbo-aware, chat messages
├── role.py         # Role detection + ROLE_PROFILES (pos 1–5)
└── stratz.py       # Stratz GraphQL client — get_match_positions() for pos 1–5 roles

static/
├── index.html      # Single-page frontend with chat panel
└── style.css       # Dark theme styles

tests/
├── test_chat.py        # Chat message building + truncation (12 tests)
├── test_detector.py    # Error detection v1 + v2 percentile (46 tests)
├── test_enricher.py    # Enricher with mocked HTTP + caching (14 tests)
├── test_extractor.py   # Metric extraction from parser records (25 tests)
├── test_prompt.py      # Prompt rendering for all roles (28 tests)
├── test_role.py        # Role detection + profiles (27 tests)
└── test_stratz.py      # Stratz client — positions, fallbacks, error cases (8 tests)
```

## Testing

```bash
# Run all 159 tests
pytest tests/ -v

# Run a specific test file
pytest tests/test_detector.py -v
```

## License

Private — not yet licensed for distribution.
