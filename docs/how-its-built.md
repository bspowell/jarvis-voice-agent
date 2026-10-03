# How Jarvis is built

## Big picture
- **Ears**: wake word + speech-to-text (Deepgram Flux)
- **Brain**: Jarvis orchestrator (Opus 5.5) → sub-agents (Notes, Calendar, Search) → tools
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
| `src/jarvis/cli.py` | The `jarvis` command (Typer) |
| `tests/` | pytest tests |
| `docs/` | This map |
| `pyproject.toml` · `uv.lock` | Dependencies; the lock pins exact versions |

## Config
- `.env`: secrets, gitignored · `.env.example`: template, committed
- `jarvis.toml`: preferences (models, places, timings), committed
- Priority: env vars > `.env` > `jarvis.toml` > code defaults
- `SecretStr` hides keys in logs; `jarvis doctor` shows only set/missing

## Tooling
- `uv`: Python + packages + lock file; `uv run <cmd>` runs inside the project
- `ruff`: lint + format · `pytest`: tests · `gh`: GitHub
- `livekit-server` + `lk`: local voice server and CLI (Phase 7+)

## Roadmap
| # | Phase | Status |
|---|---|---|
| 0 | Foundation | ✅ |
| 1 | Brain v0: text chat, persona, tracing | ⏳ |
| 2 | First tool: weather + LangGraph Studio | |
| 3 | Memory: daily thread + long-term facts | |
| 4 | Search sub-agent | |
| 5 | Calendar sub-agent + sign-in + confirmations | |
| 6 | Notes sub-agent: Notion MCP + files | |
| 7 | Voice agent (LiveKit) | |
| 8 | Wake-word gate + follow-ups | |
| 9 | Desktop app + barge-in | |
| 10 | Holographic orb overlay | |
| 11 | Info cards + sign-in card | |
| 12 | Background app + menu bar | |
| 13 | Evals + tuning (optional) | |
