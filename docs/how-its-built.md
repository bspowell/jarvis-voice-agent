# How Jarvis is built

## Big picture
- **Ears**: speech-to-text + end of turn (Deepgram Flux); wake word in Phase 8
- **Brain**: Jarvis orchestrator (Sonnet 5) → sub-agents (Notes, Calendar, Search) → tools
- **Mouth**: text-to-speech (ElevenLabs Jarvis voice, `eleven_v4_turbo`; Kokoro offline fallback)
- **Face**: holographic orb overlay + info cards

```
Desktop app (mic, speaker, overlay) ⇄ LiveKit server (local) ⇄ Voice agent (ears + mouth) → Brain
Today: `lk agent console` → Voice agent (your mic + speakers) → Brain
Text mode: `jarvis chat` → Brain directly
```

## Project layout
| Path | Purpose |
|---|---|
| `src/jarvis/` | The app. src layout: tests import the installed package, never loose files |
| `src/jarvis/config.py` | Typed settings: secrets from `.env`, preferences from `jarvis.toml` |
| `src/jarvis/cli.py` | The `jarvis` command (Typer): `chat` (incl. the sign-in buttons), `login` / `logout` (`google` or `notion`), `doctor` (keys, sign-ins, models, sub-agent health) |
| `src/jarvis/events.py` | Event bus: tools announce what's happening (e.g. a sub-agent started); chat / voice / orb decide how to show it |
| `src/jarvis/prompts/` | System prompts as Markdown (`jarvis.md` = his personality · `search_agent.md` · `calendar_agent.md` · `notes_agent.md` · `approval_question.md` / `approval_reply.md` = asking "Shall I…?" and reading the answer · `summary.md` = how long days are condensed) |
| `src/jarvis/brain/` | `models.py` (model settings) · `jarvis.py` (the agent) · `brain.py` (the doorway) · `approvals.py` (confirmations) · `memory.py` (SQLite memory + its middleware) · `greeting.py` · `graph.py` (Studio entry) |
| `src/jarvis/agents/` | Sub-agents, each wrapped as one tool for Jarvis: `search.py` · `calendar.py` · `notes.py` (Notion + files) · `writes.py` (approvals + receipts, shared) · `registry.py` (the list + roll call) |
| `src/jarvis/tools/` | Tools the model can call: `weather.py` · `location.py` (home city / IP / place names) · `memory_tools.py` (remember / recall / forget) · `web_search.py` (Firecrawl, for the Search agent) · `google_calendar.py` (for the Calendar agent) · `notion.py` (Notion's MCP tools + ours) · `files.py` (the sandbox) · `errors.py` |
| `src/jarvis/voice/` | The voice agent (LiveKit): `worker.py` (one session: memory, roll call, pipeline) · `jarvis_agent.py` (`llm_node` → Brain, filler, unheard questions, sign-ins) · `speech.py` (Flux + ElevenLabs) · `kokoro_tts.py` (offline fallback voice) · `phrases.py` (recorded fixed lines) · `health.py` (`doctor` checks) |
| `agent.py` | Where LiveKit's CLI finds the voice agent (`lk agent console` / `debugger`) |
| `src/jarvis/auth/` | Sign-ins: `google.py` (browser consent, PKCE, silent renewal) · `notion.py` (Notion's MCP OAuth: self-registration, PKCE) · `store.py` (macOS Keychain + the 30-day rule) · `accounts.py` (names + browser sign-in shared by chat and voice) |
| `langgraph.json` | Tells `langgraph dev` (Studio) where Jarvis's graph is |
| `tests/` | pytest tests |
| `docs/` | This map |
| `data/` | Runtime data, gitignored: `jarvis.db` = conversations + remembered facts · `voice/phrases/` = recorded fixed lines · `voice/kokoro/` = the fallback voice's model |
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
          └─ a write → paused (interrupt) → Brain asks "Shall I…?" (the exact change printed under it) → your next message is the answer
     ↳ notes_agent → Notes agent (Haiku 4.5) ⇄ Notion (MCP) + your files (sandbox) → report + receipt
          ├─ pages it reads or writes open in the Notion app
          ├─ Notion not connected → event "sign_in_needed" → "▸ Connect Notion"
          └─ writes (create · append · move) pause for your OK, like the calendar
  → streamed chunks → printed as they arrive
  → checkpointer saves the thread to data/jarvis.db · LangSmith records the trace (sub-agent nested inside)
```
- Boot greeting: plain Python, instant + free. A roll call (health checks) counts the sub-agents present.

## Voice: one spoken turn
```
You speak → Deepgram Flux (text + "you've finished") → 0.3 s → JarvisAgent.llm_node
  → Brain.turn(text)   ← exactly as chat calls it: same thread, memory, approvals
     ├─ sub-agent starts → "Right away, sir." + flush → plays now, from its recording
     └─ his words stream back → ElevenLabs (24 kHz PCM) → your speakers, sentence by sentence
  ↳ ElevenLabs fails → Kokoro (offline) takes over at once
  ↳ "Shall I…?" talked over → withdrawn: your next words are a new message
  ↳ sign-in needed → the browser opens → signed in → he carries on
```
- Preemptive generation off: every Brain turn is real (saved, may start sub-agents), so no speculative guesses.

## Config
- `.env`: secrets, gitignored · `.env.example`: template, committed. Includes Jarvis's Google sign-in app (`GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`)
- macOS Keychain: your Google and Notion tokens (entries `jarvis` / `google`, `jarvis` / `notion`), never in a file
- `jarvis.toml`: preferences (models, `[location] home_city`, `[memory] new_day_hour`, `[auth] remember_days`, `[files] folders`, `[notion] open_pages`, `[voice] stt_model / tts_model / fallback_voice`), committed
- `data/jarvis.db`: memory (today's conversation + facts), gitignored
- Priority: env vars > `.env` > `jarvis.toml` > code defaults
- `SecretStr` hides keys in logs; `jarvis doctor` shows only set/missing

## Tooling
- `uv`: Python + packages + lock file; `uv run <cmd>` runs inside the project
- `ruff`: lint + format · `pytest`: tests · `gh`: GitHub
- `langgraph dev`: LangGraph Studio, a visual debugger for the agent graph
- `lk agent console`: talk to the voice agent with your mic + speakers (echo cancellation on) · `lk agent debugger`: typed turns, no audio, prints replies + timings
- `livekit-server`: the local voice server (Phase 9, for the desktop app)
