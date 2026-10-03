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
| `src/jarvis/prompts/` | System prompts as Markdown (`jarvis.md` = his personality) |
| `src/jarvis/brain/` | `models.py` (model settings) · `jarvis.py` (the agent) · `brain.py` (the doorway) · `greeting.py` · `graph.py` (Studio entry) |
| `src/jarvis/tools/` | Tools the model can call: `weather.py` · `location.py` (home city / IP / place names) · `errors.py` |
| `langgraph.json` | Tells `langgraph dev` (Studio) where Jarvis's graph is |
| `tests/` | pytest tests |
| `docs/` | This map |
| `pyproject.toml` · `uv.lock` | Dependencies; the lock pins exact versions |

## The brain: one chat turn
```
You type → jarvis chat → Brain.turn(text)
  → adds "[Saturday 3 October 2026, 2:05 PM PDT]" to your message
  → Jarvis agent (Sonnet 5 + persona) → streamed chunks → printed as they arrive
     ↳ needs live facts? model → get_weather → model (tool loop)
  → checkpointer saves the thread · LangSmith records the trace
```
- Boot greeting: plain Python, instant + free.

## Config
- `.env`: secrets, gitignored · `.env.example`: template, committed
- `jarvis.toml`: preferences (models, `[location] home_city`, timings), committed
- Priority: env vars > `.env` > `jarvis.toml` > code defaults
- `SecretStr` hides keys in logs; `jarvis doctor` shows only set/missing

## Tooling
- `uv`: Python + packages + lock file; `uv run <cmd>` runs inside the project
- `ruff`: lint + format · `pytest`: tests · `gh`: GitHub
- `langgraph dev`: LangGraph Studio, a visual debugger for the agent graph
- `livekit-server` + `lk`: local voice server and CLI (Phase 7+)
