# How Jarvis is built

## Big picture
- **Ears**: wake word + speech-to-text (Deepgram Flux)
- **Brain**: Jarvis orchestrator (Sonnet 5) → sub-agents (Notes, Calendar, Search) → tools
- **Mouth**: text-to-speech (ElevenLabs Jarvis voice, Kokoro fallback)
- **Face**: holographic orb overlay + info cards

```
Desktop app (mic, speaker, overlay) ⇄ LiveKit server (local) ⇄ Voice agent (ears + mouth) → Brain
Text mode: `jarvis chat` → Brain directly
```

## Project layout
| Path | Purpose |
|---|---|
| `src/jarvis/` | The app. src layout: tests import the installed package, never loose files |
| `src/jarvis/config.py` | Typed settings: secrets from `.env`, preferences from `jarvis.toml` |
| `src/jarvis/cli.py` | The `jarvis` command (Typer): `chat`, `doctor` |
| `src/jarvis/prompts/` | System prompts as Markdown (`jarvis.md` = his personality · `summary.md` = how long days are condensed) |
| `src/jarvis/brain/` | `models.py` (model settings) · `jarvis.py` (the agent) · `brain.py` (the doorway) · `memory.py` (SQLite memory + its middleware) · `greeting.py` · `graph.py` (Studio entry) |
| `src/jarvis/tools/` | Tools the model can call: `weather.py` · `location.py` (home city / IP / place names) · `memory_tools.py` (remember / recall / forget) · `errors.py` |
| `langgraph.json` | Tells `langgraph dev` (Studio) where Jarvis's graph is |
| `tests/` | pytest tests |
| `docs/` | This map |
| `data/` | Runtime data, gitignored: `jarvis.db` = conversations + remembered facts |
| `pyproject.toml` · `uv.lock` | Dependencies; the lock pins exact versions |

## The brain: one chat turn
```
You type → jarvis chat → Brain.turn(text)
  → picks today's thread (jarvis-2026-10-03) + adds "[Saturday 3 October 2026, 2:05 PM PDT]" to your message
  → Jarvis agent: repair → (summarize, if the day is huge) → model (Sonnet 5 + persona) ⇄ tools
     ↳ tools: get_weather · remember / recall / forget facts (store)
  → streamed chunks → printed as they arrive
  → checkpointer saves the thread to data/jarvis.db · LangSmith records the trace
```
- Boot greeting: plain Python, instant + free.

## Config
- `.env`: secrets, gitignored · `.env.example`: template, committed
- `jarvis.toml`: preferences (models, `[location] home_city`, `[memory] new_day_hour`, timings), committed
- `data/jarvis.db`: memory (today's conversation + facts), gitignored
- Priority: env vars > `.env` > `jarvis.toml` > code defaults
- `SecretStr` hides keys in logs; `jarvis doctor` shows only set/missing

## Tooling
- `uv`: Python + packages + lock file; `uv run <cmd>` runs inside the project
- `ruff`: lint + format · `pytest`: tests · `gh`: GitHub
- `langgraph dev`: LangGraph Studio, a visual debugger for the agent graph
- `livekit-server` + `lk`: local voice server and CLI (Phase 7+)
