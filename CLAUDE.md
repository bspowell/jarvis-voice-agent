# Jarvis: project guide for Claude Code

## Stack
- Python 3.13 · uv (`uv add`, `uv run`) · src layout (`src/jarvis/`)
- Brain: LangChain v1 `create_agent` + LangGraph · tracing: LangSmith
- Voice: LiveKit Agents (local server) · Deepgram Flux · ElevenLabs (+ Kokoro fallback)
- Architecture + roadmap: `docs/how-its-built.md`

## Rules
- Clear names, small modules, type hints. `ruff check`, `ruff format --check`, `pytest` must pass.
- Secrets only in `.env` (gitignored); preferences in `jarvis.toml`.
- Voice code never imports LangGraph: it only calls `Brain.turn()`.
- Never write LangChain or LiveKit code from memory: load the matching skill / docs MCP first.
- Opus 5.5: frozen system prompt (time goes in user messages), no `temperature`, no forced `tool_choice`.
- Writes need user confirmation (HITL); no delete tools.
