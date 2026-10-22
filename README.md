# Jarvis

A dry-witted voice AI butler: LangChain + LangGraph brain, LiveKit voice, holographic overlay.

## Setup
Needs a Mac with [Homebrew](https://brew.sh), plus:
- Xcode's command line tools: `xcode-select --install`
- [uv](https://docs.astral.sh/uv/): `brew install uv`
- LiveKit's local server: `brew install livekit`

```bash
git clone https://github.com/bspowell/jarvis-voice-agent.git
cd jarvis-voice-agent
uv sync
cp .env.example .env   # then add your keys (below)
uv run jarvis doctor   # what's ready, what's missing
uv run jarvis install  # builds Jarvis.app and opens it: his icon appears in the menu bar
```

Keys in `.env`:
- Needed: `ANTHROPIC_API_KEY` (his brain), `DEEPGRAM_API_KEY` (his ears), `ELEVEN_API_KEY` and
  `ELEVENLABS_VOICE_ID` (his voice)
- Optional: `FIRECRAWL_API_KEY` (web search), `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` (Calendar and
  to-dos), `LANGSMITH_API_KEY` (tracing). Notion needs no key.

## Talking to Jarvis
1. Click his icon in the menu bar → **Start Jarvis** (or press ⌃⌥J). The first time, macOS asks for the
   microphone. He greets you, and his orb appears.
2. Say **"Hey Jarvis"**, then what you need (in one go is fine: "Hey Jarvis, what's the weather?").
   After he answers, you have 6 seconds to carry on; after that, say "Hey Jarvis" again.
3. Say **"wait"** - he stops talking but keeps listening ("wait, make it 4 instead").
4. Say **"stop"** - he stops talking and stops listening. Say "Hey Jarvis" to start the conversation again.
5. To mute Jarvis - not listening to you at all (even if you said "Hey Jarvis") - use the Mute button from the menu.

Things to ask:
- "What's the weather this weekend?"
- "What's on my calendar tomorrow?" · "Move my 3 o'clock to Friday."
- "Remind me to renew my passport by Friday."
- "Find my notes on the garden."
- "Search the web for a good banana bread recipe."
- "Remember that I take my coffee black."
- "Hide the orb." · "Clear the cards."

He asks before he changes your calendar, notes or files: say yes, no, or what to change ("yes, but at 4").
When he needs your Google or Notion account, a card with a **Sign in** button appears beside the orb.

## The menu
| Item | What it does |
|---|---|
| Start Jarvis / Stop Jarvis | Turn him on or off |
| Talk to Jarvis (⌃⌥J) | He listens now, no "Hey Jarvis" needed; if he's talking, he stops |
| Mute Jarvis (⌃⌥M) | Mutes your mic until you unmute it |
| Hide Orb / Show Orb | The orb on your screen |
| Orb Visual | What's in the orb's middle |
| Start Options | At login: start Jarvis, only show his icon (the default), or nothing |
| Google, Notion | Connect or disconnect an account (your browser opens) |
| Quit Jarvis (⌘Q) | Close the app |

The menu's first line says what he's doing (asleep, listening, thinking, speaking, muted). If he stopped by
himself, it says why, and **Show Log** opens his log.

## Other commands
| Command | What it does |
|---|---|
| `uv run jarvis up` | Start Jarvis from the terminal, with his logs there (quit Jarvis.app first; Ctrl+C stops him) |
| `uv run jarvis chat` | Type to Jarvis instead of talking |
| `uv run jarvis desktop --demo` | Preview the orb and cards with made-up data (no keys needed) |
| `uv run jarvis login` / `logout` | Sign in to or out of Google from the terminal (`login notion` for Notion) |
| `uv run jarvis uninstall` | Remove Jarvis.app |
| `uv run pytest` | Run the tests |
| `uv run langgraph dev` | LangGraph Studio: Jarvis's graph in the browser |

## Docs
- [How it's built](docs/how-its-built.md): architecture map
