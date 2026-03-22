You are helping me build a Dota 2 personal coach tool. Your task is to produce a complete PRD (Product Requirements Document) — not code yet.

## Context

I am a Crusader/Archon carry player (pos 1) who wants to improve my MMR. I want a tool that:
1. Downloads my recent match replay files (.dem.bz2) from the Valve CDN via OpenDota API
2. Parses them using the odota parser to extract high-level coaching-relevant data
3. Detects concrete mistakes based on carry-specific thresholds
4. Sends the structured data to an LLM which explains in plain language what went wrong and what to fix

## Stack

Choose tools based on library quality and ecosystem maturity:

- **Data fetching**: Python with httpx (async) and pydantic for type-safe modeling of OpenDota responses
- **Replay parsing**: odota parser (https://github.com/odota/parser) running as a local HTTP sidecar. Reason: it already exposes high-level features (item build times, laning position heatmap, ward placement map, LH per minute table, gold/XP graphs per minute, teamfight summary, objective times) without requiring callback-level programming. If per-tick positional data is needed in v2, Clarity can be added without changing the rest of the architecture.
- **LLM integration**: LiteLLM — not LangChain. Reason: LiteLLM is a thin provider-agnostic adapter that translates any call to OpenAI-compatible format, supporting Anthropic, OpenAI, and local models via Ollama. LangChain solves pipeline complexity (RAG, multi-agent, memory) — none of which we need. Our complexity is in the analysis logic and prompt design, not the LLM pipeline.
- **Model config**: model selection must be configurable at runtime via env var (LLM_MODEL) or CLI flag (--model). Supported format: "anthropic/claude-sonnet-4-6", "openai/gpt-4o", "ollama/llama3". No hardcoded model names anywhere in business logic.
- **CLI**: Typer with Rich for terminal output
- No over-engineering: this is a personal tool, not a SaaS product

## What the PRD must include

1. **Goals and non-goals** — what v1 does and explicitly does not do

2. **Architecture** — data flow from OpenDota API → .dem.bz2 download → odota parser (HTTP sidecar) → pydantic extractor → error detector → LiteLLM → Rich terminal output. Include an ASCII diagram.

3. **Data model** — what fields we extract from the odota parser JSON output and why. Focus on carry-relevant metrics available from odota parser:
   - GPM and XPM
   - Last hits per minute table (especially at 10min)
   - Deaths during laning phase (0–10min), with timestamps
   - Net worth delta vs enemy carry at 10min and 20min
   - First core item timing (minutes to purchase)
   - Laning position heatmap data (to detect passive/aggressive positioning)
   - Ward placement map (to detect whether carry bought support wards — bad habit)
   - Teamfight summary (participation rate, damage contribution)
   - Objective times (roshan, towers)

4. **Error detection logic** — concrete, opinionated thresholds for Crusader/Archon carry. Make a decision and justify it briefly. Examples:
   - < 45 last hits at 10min = poor laning
   - > 2 deaths before 10min = unsafe laning
   - First core item after 18min = slow farm
   - Teamfight participation < 40% = farming while team fights
   - Laning heatmap concentrated in own half at 8min = not contesting

5. **LLM prompt design** — the exact system prompt and user message structure:
   - System prompt: concise, direct Dota 2 carry coach persona
   - User message: only extracted metrics (never raw JSON), structured as a compact summary
   - Output format: 3 concrete mistakes ranked by impact + 1 priority focus for next game
   - Total prompt budget: under 800 tokens per analysis

6. **odota parser integration** — how to run it as a local Docker sidecar, how to call it from Python (POST the replay URL, parse the JSON response), and how to handle the cases where the replay has expired on Valve's CDN (replays expire after ~7–10 days)

7. **Open questions** — things that need a decision before implementation starts

## Constraints

- OpenDota API: free, 60 req/min unauthenticated. Design around this.
- Valve CDN replay availability: ~7–10 days. The tool must check availability before attempting download and surface a clear error if expired.
- LLM prompt must be compact — extracted metrics only, never raw odota JSON (which can be 100KB+).
- No database for v1 — stateless, single-match analysis per run.
- No hardcoded model names anywhere in business logic.
- I will use Beads to break the PRD into implementation tasks after approval.

## Output format

Write the PRD in Markdown. Be specific and opinionated — make decisions and justify them briefly. Do not present options where a clear choice exists.