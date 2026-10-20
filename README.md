# Jarvis

A dry-witted voice AI butler: LangChain + LangGraph brain, LiveKit voice, holographic overlay.

## Requirements
- macOS: the orb, the menu bar and Jarvis.app use AppKit; sign-ins are kept in the Keychain
- [uv](https://docs.astral.sh/uv/) (it installs Python 3.13) and a local LiveKit server: `brew install livekit`
- API keys in `.env`: `ANTHROPIC_API_KEY` for the brain; `DEEPGRAM_API_KEY`, `ELEVEN_API_KEY` and
  `ELEVENLABS_VOICE_ID` for voice. Optional: `FIRECRAWL_API_KEY` (Search agent), `GOOGLE_CLIENT_ID` /
  `GOOGLE_CLIENT_SECRET` (Calendar + Tasks), `LANGSMITH_API_KEY` (tracing). Notion needs no key.

## Setup
1. `uv sync`: installs Python 3.13 + dependencies
2. `cp .env.example .env`: then fill in the keys (see Requirements)
3. `uv run jarvis doctor`: shows which keys Jarvis can see

## Commands
| Command | What it does |
|---|---|
| `uv run jarvis install` | Builds `~/Applications/Jarvis.app` (macOS gives it the mic) and opens it: Jarvis's icon in the menu bar. Start him there or with ⌃⌥J (talk: wakes him, or cuts him off); ⌃⌥M mutes your mic. Start Options: launch at login, show in the menu bar at login (the default), or neither. `uninstall` removes it |
| `uv run jarvis up` | The same app from a terminal, starting Jarvis at once: the local LiveKit server (`brew install livekit`), the voice agent, the orb and your mic (echo-cancelled), everyone's output here and in `data/logs/up.log`. Say "Hey Jarvis…"; after he answers, 6 s to follow up without it; "Jarvis" or "stop" cuts him off. Ctrl+C stops it all (quit Jarvis.app first: one app at a time) |
| `uv run jarvis desktop` | The menu-bar app alone (what Jarvis.app opens); `--demo` shows the orb's looks with no Jarvis |
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
