# Jarvis

A dry-witted voice AI butler: LangChain + LangGraph brain, LiveKit voice, holographic overlay.

## Setup
1. `uv sync`: installs Python 3.13 + dependencies
2. `cp .env.example .env`: then fill in keys as each phase needs them
3. `uv run jarvis doctor`: shows which keys Jarvis can see

## Commands
| Command | What it does |
|---|---|
| `uv run jarvis doctor` | Key status (set/missing) + models in use |
| `uv run pytest` | Run tests |
| `uv run ruff check . && uv run ruff format .` | Lint + format |

## Docs
- [How it's built](docs/how-its-built.md): architecture map + roadmap
