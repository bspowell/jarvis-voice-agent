# How Jarvis is built

## Big picture
- **Ears**: "Hey Jarvis" heard on your Mac (openWakeWord) → opens speech-to-text + end of turn (Deepgram Flux)
- **Brain**: Jarvis orchestrator (Sonnet 5) → sub-agents (Notes, Calendar + Tasks, Search) → tools
- **Mouth**: text-to-speech (ElevenLabs Jarvis voice, `eleven_v4_turbo`; Kokoro offline fallback)
- **Face**: holographic orb overlay + info cards

```
Desktop app (mic, speakers) ⇄ LiveKit server (on your Mac) ⇄ Voice agent (ears + mouth) → Brain
`jarvis up` starts all three · the orb overlay joins the desktop app in Phase 10
Text mode: `jarvis chat` → Brain directly
```

## Project layout
| Path | Purpose |
|---|---|
| `src/jarvis/` | The app. src layout: tests import the installed package, never loose files |
| `src/jarvis/config.py` | Typed settings: secrets from `.env`, preferences from `jarvis.toml` |
| `src/jarvis/cli.py` | The `jarvis` command (Typer): `chat` (incl. the sign-in buttons), `up` (everything voice needs), `desktop` (the desktop app alone), `login` / `logout` (`google` or `notion`), `doctor` (keys, sign-ins, models, sub-agent health, voice + LiveKit server) |
| `src/jarvis/up.py` | `jarvis up`: starts the LiveKit server, the voice agent and the desktop app in order, tags their output (also saved to `data/logs/up.log`), stops them in reverse |
| `src/jarvis/desktop/` | The desktop app: `room_client.py` (your echo-cancelled mic + speakers, a room per launch with Jarvis invited, the watchdog) · `app.py` (runs it; the orb comes in Phase 10) |
| `src/jarvis/events.py` | Event bus: tools announce what's happening (e.g. a sub-agent started); chat / voice / orb decide how to show it |
| `src/jarvis/prompts/` | System prompts as Markdown (`jarvis.md` = his personality · `search_agent.md` · `calendar_agent.md` · `notes_agent.md` · `approval_question.md` / `approval_reply.md` = asking "Shall I…?" and reading the answer · `summary.md` = how long days are condensed) |
| `src/jarvis/brain/` | `models.py` (model settings) · `jarvis.py` (the agent) · `brain.py` (the doorway) · `approvals.py` (confirmations) · `memory.py` (SQLite memory + its middleware) · `greeting.py` · `graph.py` (Studio entry) |
| `src/jarvis/agents/` | Sub-agents, each wrapped as one tool for Jarvis: `search.py` · `calendar.py` (events + to-dos) · `notes.py` (Notion + files) · `writes.py` (approvals + receipts, shared) · `registry.py` (the list + roll call) |
| `src/jarvis/tools/` | Tools the model can call: `weather.py` · `location.py` (home city / IP / place names) · `memory_tools.py` (remember / recall / forget) · `web_search.py` (Firecrawl, for the Search agent) · `google_calendar.py` + `google_tasks.py` (for the Calendar agent) · `notion.py` (Notion's MCP tools + ours) · `files.py` (the sandbox) · `errors.py` |
| `src/jarvis/voice/` | The voice agent (LiveKit): `worker.py` (one session: memory, roll call, pipeline) · `jarvis_agent.py` (`stt_node` → the gate, `llm_node` → Brain, filler, unheard questions, sign-ins) · `gate.py` (the wake-word gate: Flux on/off, follow-ups, echo guard) · `spotter.py` (wake words on your Mac) + `wakewords/` (the "stop" model) · `speech.py` (Flux + ElevenLabs) · `kokoro_tts.py` (offline fallback voice) · `phrases.py` (recorded fixed lines) · `health.py` (`doctor` checks) |
| `agent.py` | Where LiveKit's CLI finds the voice agent (`jarvis up`, `lk agent console` / `debugger`) |
| `src/jarvis/auth/` | Sign-ins: `google.py` (browser consent, PKCE, silent renewal) · `notion.py` (Notion's MCP OAuth: self-registration, PKCE) · `store.py` (macOS Keychain + the 30-day rule) · `accounts.py` (names + browser sign-in shared by chat and voice) |
| `langgraph.json` | Tells `langgraph dev` (Studio) where Jarvis's graph is |
| `tests/` | pytest tests |
| `docs/` | This map |
| `data/` | Runtime data, gitignored: `jarvis.db` = conversations + remembered facts · `voice/phrases/` = recorded fixed lines · `voice/kokoro/` = the fallback voice's model · `logs/up.log` = the last `jarvis up` run |
| `pyproject.toml` · `uv.lock` | Dependencies; the lock pins exact versions |

## The brain: one chat turn
```
You type → jarvis chat → Brain.turn(text)
  → picks today's thread (jarvis-2026-10-03) + adds "[Saturday 3 October 2026, 2:05 PM PDT]" to your message
  → Jarvis agent: repair → (summarize, if the day is huge) → model (Sonnet 5 + persona) ⇄ tools
     ↳ tools: get_weather · remember / recall / forget facts (store)
     ↳ search_agent → Search agent (Haiku 4.5 · Sonnet 5.5 deep) ⇄ Firecrawl → short report
          └─ event "agent_started" → brain.events → chat shows "Right away, sir."
     ↳ calendar_agent → Calendar agent (Haiku 4.5) ⇄ Google Calendar + Google Tasks → report + receipt
          ├─ not signed in → event "sign_in_needed" → chat shows the sign-in button
          ├─ a write → paused (interrupt) → Brain asks "Shall I…?" (the exact change printed under it) → your next message is the answer
          └─ nothing to change, or a task "at 7 AM" (Google can't store its time) → never asked: the tool refuses
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
"Hey Jarvis, what's my day look like?"
  → desktop app: your mic, echo-cancelled (WebRTC) → LiveKit server on your Mac → the voice agent's room
  → the gate (stt_node): the spotter on your Mac hears "Hey Jarvis" → Flux opens, hears the speech in the last 1.5 s first
  → Deepgram Flux (text + "you've finished", stamped with when you stopped) → JarvisAgent.llm_node
  → Brain.turn(text)   ← exactly as chat calls it: same thread, memory, approvals
     ├─ sub-agent starts → "Right away, sir." + flush → plays now, from its recording
     └─ his words stream back → ElevenLabs (24 kHz PCM) → the room → the desktop app's speakers, sentence by sentence
  ↳ he finishes → 6 s to follow up without "Hey Jarvis" (10 s after "Shall I…?") → quiet → Flux closes
  ↳ while he talks: only his name, "stop" or "wait" stop him; his own echo is dropped
  ↳ just "Hey Jarvis" → "Sir?" · just "stop" → silence
  ↳ ElevenLabs fails → Kokoro (offline) takes over at once
  ↳ "Shall I…?" talked over → withdrawn: your next words are a new message
  ↳ sign-in needed → the browser opens → signed in → he carries on
  ↳ his session ends (you quit, or it fails) → the room is deleted → a desktop still open starts a new one
```
- Measured: you stop → he speaks ~2.1 s on a follow-up, ~3.5 s after waking (weather, a tool call). Flux is in the cloud: a busy network makes him late, and the log says so.
- Flux (paid, ~$0.39/h connected) only runs while you're talking to him; the wake word is free and stays on your Mac.
- Preemptive generation off: every Brain turn is real (saved, may start sub-agents), so no speculative guesses.

## Config
- `.env`: secrets, gitignored · `.env.example`: template, committed. Includes Jarvis's Google sign-in app (`GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`) and the LiveKit server (`LIVEKIT_URL` / `_API_KEY` / `_API_SECRET`; the defaults are the local dev server's fixed, public ones)
- macOS Keychain: your Google and Notion tokens (entries `jarvis` / `google`, `jarvis` / `notion`), never in a file
- `jarvis.toml`: preferences (models, `[location] home_city`, `[memory] new_day_hour`, `[auth] remember_days`, `[files] folders`, `[notion] open_pages`, `[voice] stt_model / tts_model / fallback_voice / follow_up_seconds`), committed
- Google Cloud (Jarvis's Google app): Calendar API + Tasks API switched on; sign-in scopes: calendar events, your calendar list, tasks
- `data/jarvis.db`: memory (today's conversation + facts), gitignored
- Priority: env vars > `.env` > `jarvis.toml` > code defaults
- `SecretStr` hides keys in logs; `jarvis doctor` shows only set/missing

## Tooling
- `uv`: Python + packages + lock file; `uv run <cmd>` runs inside the project
- `ruff`: lint + format · `pytest`: tests · `gh`: GitHub
- `langgraph dev`: LangGraph Studio, a visual debugger for the agent graph
- `jarvis up`: the LiveKit server + voice agent + desktop app, colour-tagged output; Ctrl+C (or closing the window) stops them in reverse
- `lk agent console`: the voice agent alone, with your mic + speakers in the terminal · `lk agent debugger`: typed turns, no audio, prints replies + timings
- `livekit-server --dev`: the local LiveKit server (Homebrew `livekit`); `jarvis up` starts it unless one is running
