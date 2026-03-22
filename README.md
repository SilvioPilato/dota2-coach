# Dota 2 Personal Coach

AI-powered coaching tool that analyzes Dota 2 replays and delivers role-aware, percentile-based feedback for all 5 positions. Available as a CLI and a web UI.

## Features

- **All 5 roles** — auto-detected via OpenDota `lane_role`, with manual override
- **Percentile-based error detection** — severity from global OpenDota benchmarks (critical < 20th, high < 35th, medium < 45th)
- **Role-aware metrics** — each position observes different KPIs (GPM/LH for cores, wards/stacks/healing for supports)
- **Turbo mode** — detected automatically (`game_mode == 23`), uses absolute metrics with turbo-calibrated reference values
- **Patch context** — current item costs and hero base stats injected into prompts via `dotaconstants`
- **Follow-up chat** — ask the coach questions about your match via streaming SSE
- **Web UI** — dark-themed single-page app with metric cards, benchmark bars, coaching report, and chat panel
- **CLI** — terminal interface with Rich formatting

## Architecture

```
Browser / CLI
    │
    ▼
FastAPI backend (api.py)
    │
    ├─► OpenDota API        → match metadata + replay URL
    ├─► Valve CDN           → .dem.bz2 replay file
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
| `DOTA_COACH_TOKEN_BUDGET` | `800` | Max tokens for LLM user message |
| `DOTA_COACH_LH_AT_10_MIN` | `45` | Min last hits at 10 min (v1 threshold) |
| `DOTA_COACH_DEATH_LIMIT` | `2` | Max deaths before 10 min |
| `DOTA_COACH_EARLY_DEATH_MINUTES` | `5.0` | Early death cutoff (minutes) |
| `DOTA_COACH_SLOW_CORE_ITEM_MINUTES` | `18.0` | Slow first core item threshold |
| `DOTA_COACH_NW_DEFICIT_AT_10` | `1000` | Net worth deficit threshold at 10 min |
| `DOTA_COACH_NW_DEFICIT_AT_20` | `2500` | Net worth deficit threshold at 20 min |
| `DOTA_COACH_PASSIVE_LANING_PCT` | `0.70` | Own-half positioning fraction |
| `DOTA_COACH_WARD_PURCHASE_LIMIT` | `2` | Ward purchases to flag (carry) |
| `DOTA_COACH_TF_PARTICIPATION_FLOOR` | `0.40` | Min teamfight participation |

## Usage

### Web UI

```bash
uvicorn dota_coach.api:app --reload
```

Open [http://localhost:8000](http://localhost:8000), enter a Match ID and optionally a Player ID, then click **Analyze**.

After the coaching report loads, use the chat panel below to ask follow-up questions.

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
  "role_override": 1
}
```

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
├── cli.py          # Typer CLI — analyze, recent commands
├── coach.py        # LiteLLM calls — get_coaching(), stream_llm()
├── config.py       # Environment-based configuration
├── detector.py     # Error detection — percentile-based + absolute rules
├── downloader.py   # Replay download from Valve CDN
├── enricher.py     # Benchmark + patch data fetcher with disk cache
├── extractor.py    # Parser NDJSON → MatchMetrics, build_timeline()
├── models.py       # Pydantic models — MatchMetrics, MatchReport, ChatRequest, etc.
├── opendota.py     # OpenDota API client — get_match(), get_benchmarks()
├── parser.py       # odota parser sidecar client
├── prompt.py       # LLM prompt builder — role-aware, turbo-aware, chat messages
└── role.py         # Role detection + ROLE_PROFILES (pos 1–5)

static/
├── index.html      # Single-page frontend with chat panel
└── style.css       # Dark theme styles

tests/
├── test_chat.py        # Chat message building + truncation (12 tests)
├── test_detector.py    # Error detection v1 + v2 percentile (46 tests)
├── test_enricher.py    # Enricher with mocked HTTP + caching (14 tests)
├── test_extractor.py   # Metric extraction from parser records (25 tests)
├── test_prompt.py      # Prompt rendering for all roles (28 tests)
└── test_role.py        # Role detection + profiles (27 tests)
```

## Testing

```bash
# Run all 151 tests
pytest tests/ -v

# Run a specific test file
pytest tests/test_detector.py -v
```

## License

Private — not yet licensed for distribution.
