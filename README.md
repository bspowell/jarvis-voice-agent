# Jarvis

A dry-witted voice AI butler: LangChain + LangGraph brain, LiveKit voice, holographic overlay.

## Setup
1. `uv sync`: installs Python 3.13 + dependencies
2. `cp .env.example .env`: then fill in keys as each phase needs them
3. `uv run jarvis doctor`: shows which keys Jarvis can see

## Commands
| Command | What it does |
|---|---|
| `uv run jarvis up` | Talk to Jarvis by voice: starts the local LiveKit server (`brew install livekit`), the voice agent and the desktop app (your mic + speakers, echo-cancelled). Say "Hey Jarvis…"; after he answers, 6 s to follow up without it; "Jarvis" or "stop" cuts him off. Ctrl+C stops it all; the output is also in `data/logs/up.log` |
| `uv run jarvis desktop` | Just the desktop app (needs the server and agent running) |
| `lk agent console` | The voice agent alone, with your mic and speakers in the terminal (`m` mute, `Ctrl+T` type, `q` quit) |
| `lk agent debugger start` / `say "…"` / `stop` | Drive the voice agent with typed turns (no audio): replies + timings |
| `uv run jarvis chat` | Talk to Jarvis in the terminal (remembers today's conversation; Search, Calendar (+ to-dos) and Notes agents; asks before any change) |
| `uv run jarvis doctor` | Key status (set/missing), sign-ins, models in use, sub-agent health, voice checks (ElevenLabs credits left, LiveKit server running) |
| `uv run jarvis login [google\|notion]` / `logout` | Sign in to / out of Google (default) or Notion (chat also offers a button the first time) |
| `uv run langgraph dev` | LangGraph Studio: see and run Jarvis's graph in the browser |
| `uv run pytest` | Run tests |
| `uv run ruff check . && uv run ruff format .` | Lint + format |

## Docs
- [How it's built](docs/how-its-built.md): architecture map
