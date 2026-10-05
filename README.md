# Jarvis

A dry-witted voice AI butler: LangChain + LangGraph brain, LiveKit voice, holographic overlay.

## Setup
1. `uv sync`: installs Python 3.13 + dependencies
2. `cp .env.example .env`: then fill in keys as each phase needs them
3. `uv run jarvis doctor`: shows which keys Jarvis can see

## Commands
| Command | What it does |
|---|---|
| `uv run jarvis chat` | Talk to Jarvis in the terminal (remembers today's conversation; Search and Calendar agents; asks before any change) |
| `uv run jarvis doctor` | Key status (set/missing), sign-ins, models in use, sub-agent health checks |
| `uv run jarvis login [google\|notion]` / `logout` | Sign in to / out of Google (default) or Notion (chat also offers a button the first time) |
| `uv run langgraph dev` | LangGraph Studio: see and run Jarvis's graph in the browser |
| `uv run pytest` | Run tests |
| `uv run ruff check . && uv run ruff format .` | Lint + format |

## Docs
- [How it's built](docs/how-its-built.md): architecture map
