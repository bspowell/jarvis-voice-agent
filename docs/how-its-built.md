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
| `src/jarvis/cli.py` | The `jarvis` command (Typer): `chat` (incl. the sign-in button), `login` / `logout`, `doctor` (keys, sign-ins, models, sub-agent health) |
| `src/jarvis/events.py` | Event bus: tools announce what's happening (e.g. a sub-agent started); chat / voice / orb decide how to show it |
| `src/jarvis/prompts/` | System prompts as Markdown (`jarvis.md` = his personality · `search_agent.md` · `calendar_agent.md` · `approval_question.md` / `approval_reply.md` = asking "Shall I…?" and reading the answer · `summary.md` = how long days are condensed) |
| `src/jarvis/brain/` | `models.py` (model settings) · `jarvis.py` (the agent) · `brain.py` (the doorway) · `approvals.py` (confirmations) · `memory.py` (SQLite memory + its middleware) · `greeting.py` · `graph.py` (Studio entry) |
| `src/jarvis/agents/` | Sub-agents, each wrapped as one tool for Jarvis: `search.py` · `calendar.py` (writes pause for your OK) · `registry.py` (the list + roll call) |
| `src/jarvis/tools/` | Tools the model can call: `weather.py` · `location.py` (home city / IP / place names) · `memory_tools.py` (remember / recall / forget) · `web_search.py` (Firecrawl, for the Search agent) · `google_calendar.py` (for the Calendar agent) · `errors.py` |
| `src/jarvis/auth/` | Sign-ins: `google.py` (browser consent, PKCE, silent renewal) · `store.py` (macOS Keychain + the 30-day rule) |
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
     ↳ search_agent → Search agent (Haiku 4.5 · Sonnet 5.5 deep) ⇄ Firecrawl → short report
          └─ event "agent_started" → brain.events → chat shows "Right away, sir."
     ↳ calendar_agent → Calendar agent (Haiku 4.5) ⇄ Google Calendar → report + receipt
          ├─ not signed in → event "sign_in_needed" → chat shows the sign-in button
          └─ a write → paused (interrupt) → Brain asks "Shall I…?" → your next message is the answer
  → streamed chunks → printed as they arrive
  → checkpointer saves the thread to data/jarvis.db · LangSmith records the trace (sub-agent nested inside)
```
- Boot greeting: plain Python, instant + free. A roll call (health checks) counts the sub-agents present.

## Config
- `.env`: secrets, gitignored · `.env.example`: template, committed. Includes Jarvis's Google sign-in app (`GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`)
- macOS Keychain: your Google tokens (entry `jarvis` / `google`), never in a file
- `jarvis.toml`: preferences (models, `[location] home_city`, `[memory] new_day_hour`, `[auth] remember_days`), committed
- `data/jarvis.db`: memory (today's conversation + facts), gitignored
- Priority: env vars > `.env` > `jarvis.toml` > code defaults
- `SecretStr` hides keys in logs; `jarvis doctor` shows only set/missing

## Tooling
- `uv`: Python + packages + lock file; `uv run <cmd>` runs inside the project
- `ruff`: lint + format · `pytest`: tests · `gh`: GitHub
- `langgraph dev`: LangGraph Studio, a visual debugger for the agent graph
- `livekit-server` + `lk`: local voice server and CLI (Phase 7+)
