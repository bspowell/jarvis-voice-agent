# How Jarvis is built

## Big picture
- **Ears**: "Hey Jarvis" heard on your Mac (openWakeWord) → opens speech-to-text + end of turn (Deepgram Flux)
- **Brain**: Jarvis orchestrator (Sonnet 5) → sub-agents (Notes, Calendar + Tasks, Search) → tools
- **Mouth**: text-to-speech (ElevenLabs Jarvis voice, `eleven_v4_turbo`; Kokoro offline fallback)
- **Face**: the orb, a holographic overlay on your screen (AppKit window + WebGL), with cards beside it: the weather,
  your day, to-dos, pages for further reading, sign-in buttons
- **Home**: a menu-bar icon (Jarvis.app, opened at login) that starts and stops the rest; ⌃⌥J talks to him, ⌃⌥M mutes

```
Jarvis.app → Desktop app (menu-bar icon, orb, mic, speakers) ⇄ LiveKit server (on your Mac) ⇄ Voice agent → Brain
the desktop app starts the server and the agent when you start Jarvis · `jarvis up`: the same, from a terminal
Text mode: `jarvis chat` → Brain directly
```

## Project layout
| Path | Purpose |
|---|---|
| `src/jarvis/` | The app. src layout: tests import the installed package, never loose files |
| `src/jarvis/config.py` | Typed settings: secrets from `.env`, preferences from `jarvis.toml` |
| `src/jarvis/cli.py` | The `jarvis` command (Typer): `chat` (incl. the sign-in buttons), `desktop` (the menu-bar app: what Jarvis.app opens), `up` (the same, starting Jarvis at once, output in the terminal), `install` / `uninstall` (Jarvis.app + Open at Login), `login` / `logout` (`google` or `notion`), `doctor` (keys, sign-ins, models, sub-agent health, voice + LiveKit server) |
| `src/jarvis/up.py` | The crew: the LiveKit server and the voice agent, which the desktop app starts in order and stops in reverse; their tagged output (saved to `data/logs/up.log`); parts a crashed app left running are stopped at the next launch |
| `src/jarvis/desktop/` | The desktop app: `app.py` (wiring: the menu bar, the orb, the shortcuts on the main thread; one copy at a time) · `controls.py` (start / stop / mute / talk / sign-ins: what the menu and shortcuts do) · `line.py` (the second thread: an asyncio loop for everything that waits) · `menu.py` (what the menu says, plain Python) + `menu_bar.py` (the icon and menu, AppKit) · `hotkeys.py` (⌃⌥J, ⌃⌥M: Carbon hot keys) · `install.py` + `launcher.c` (Jarvis.app and its LaunchAgent) · `room_client.py` (your echo-cancelled mic + speakers, a room per launch with Jarvis invited, the watchdog; tells the orb his state, whether he's awake and his voice level; answers his display commands; passes his events to the cards and your sign-in clicks to him) · `overlay.py` (the see-through, click-through window; remembers where it is and its centrepiece) · `card_panel.py` (the cards' panel beside it: takes clicks only on links and buttons, never your keyboard) · `cards.py` (what each card says) · `windows.py` (what both windows share) · `demo.py` (`jarvis desktop --demo`) · `web/` (the orb: `orb.html`, `orb.js` = rings + WebGL, `cores.js` = the 3D centrepieces; the cards: `cards.html`, `cards.css`, `cards.js`) |
| `src/jarvis/display.py` | The orb's display commands (show / hide / move / switch / clear the cards), and the names the voice agent and desktop app use for cards and sign-in clicks |
| `src/jarvis/events.py` | Event bus: tools announce what's happening (a sub-agent started, the weather, your agenda…); chat / voice / the cards decide how to show it |
| `src/jarvis/prompts/` | System prompts as Markdown (`jarvis.md` = his personality · `search_agent.md` · `calendar_agent.md` · `notes_agent.md` · `approval_question.md` / `approval_reply.md` = asking "Shall I…?" and reading the answer · `summary.md` = how long days are condensed) |
| `src/jarvis/brain/` | `models.py` (model settings) · `jarvis.py` (the agent) · `brain.py` (the doorway) · `context.py` (what each run hands his tools: the orb's controls) · `approvals.py` (confirmations) · `memory.py` (SQLite memory + its middleware) · `greeting.py` · `graph.py` (Studio entry) |
| `src/jarvis/agents/` | Sub-agents, each wrapped as one tool for Jarvis: `search.py` · `calendar.py` (events + to-dos) · `notes.py` (Notion + files) · `writes.py` (approvals + receipts, shared) · `registry.py` (the list + roll call) |
| `src/jarvis/tools/` | Tools the model can call: `weather.py` · `location.py` (home city / IP / place names) · `memory_tools.py` (remember / recall / forget) · `display.py` (show / hide / move / switch the orb) · `web_search.py` (Firecrawl, for the Search agent) · `google_calendar.py` + `google_tasks.py` (for the Calendar agent) · `notion.py` (Notion's MCP tools + ours) · `files.py` (the sandbox) · `errors.py` |
| `src/jarvis/voice/` | The voice agent (LiveKit): `worker.py` (one session: memory, roll call, pipeline) · `jarvis_agent.py` (`stt_node` → the gate, `llm_node` → Brain, filler, unheard questions, sign-ins) · `gate.py` (the wake-word gate: Flux on/off, follow-ups, echo guard) · `spotter.py` (wake words on your Mac) + `wakewords/` (the "stop" model) · `speech.py` (Flux + ElevenLabs) · `audio.py` (loudness, shared with the desktop app) · `kokoro_tts.py` (offline fallback voice) · `phrases.py` (recorded fixed lines) · `health.py` (`doctor` checks) |
| `agent.py` | Where LiveKit's CLI finds the voice agent (`jarvis up`, `lk agent console` / `debugger`) |
| `src/jarvis/auth/` | Sign-ins: `google.py` (browser consent, PKCE, silent renewal) · `notion.py` (Notion's MCP OAuth: self-registration, PKCE) · `store.py` (macOS Keychain + the 30-day rule) · `accounts.py` (names + browser sign-in shared by chat and voice) |
| `langgraph.json` | Tells `langgraph dev` (Studio) where Jarvis's graph is |
| `tests/` | pytest tests |
| `docs/` | This map |
| `data/` | Runtime data, gitignored: `jarvis.db` = conversations + remembered facts · `voice/phrases/` = recorded fixed lines · `voice/kokoro/` = the fallback voice's model · `logs/up.log` = everything since the desktop app opened · `logs/app.log` = what Jarvis.app printed elsewhere (crashes) · `orb.json` / `app.json` = the orb's and the menu's settings · `desktop.lock`, `crew.json` = one app at a time, the crew's process ids |
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
  ↳ "stop" ends the conversation: he stops talking and listening, anything half-heard is dropped
  ↳ just "Hey Jarvis" → "Sir?" · just "wait" → silence, he keeps listening
  ↳ ElevenLabs fails → Kokoro (offline) takes over at once
  ↳ "Shall I…?" talked over → withdrawn: your next words are a new message
  ↳ sign-in needed → the browser opens → signed in → he carries on
  ↳ his session ends (you quit, or it fails) → the room is deleted → a desktop still open starts a new one
```
- Measured: you stop → he speaks ~2.1 s on a follow-up, ~3.5 s after waking (weather, a tool call). Flux is in the cloud: a busy network makes him late, and the log says so.
- Flux (paid, ~$0.39/h connected) only runs while you're talking to him; the wake word is free and stays on your Mac.
- Preemptive generation off: every Brain turn is real (saved, may start sub-agents), so no speculative guesses.

## The orb: what you see
```
desktop app ─ main thread: a see-through window above everything (AppKit) → a web page drawing the orb (WebGL)
            └ the line's thread: the room → orb.update({state, awake, level})
  his state  ← LiveKit's lk.agent.state attribute      (listening / thinking / speaking)
  awake      ← the gate → the agent's jarvis.awake      ("Hey Jarvis" … 6 s of quiet, or "stop")
  level      ← his voice, measured from the audio your Mac already receives (30 times a second)
"Jarvis, hide the orb" → display_orb → his voice agent → RPC jarvis.display → the desktop does it
                       → "The orb is hidden." comes back → he answers from that, not from a guess
```
- Looks: away (grey) · asleep (small, dim, breathing) · listening (grows) · thinking (spins; a line of light sweeps
  the centrepiece) · speaking (the strands ripple with his voice).
- Centrepieces: the classic J.A.R.V.I.S. rings (default), Mjölnir on its rock, Stormbreaker, two arc reactors; your
  pick and the orb's corner are remembered in `data/orb.json`.
- Clicks go through it; it never takes your keyboard focus; ~4% of one core asleep, ~11% while you talk.

## The cards: what he's just looked up
- When a tool gets something worth showing, it publishes the facts as an event; the voice agent sends them to the
  desktop app as a text message; the desktop app draws a card beside the orb.
```
"Hey Jarvis, what's my day look like, and will I need an umbrella?"
1. the weather tool publishes the forecast · the Calendar agent publishes today's events
2. the voice agent sends both to the desktop app (text messages, topic "jarvis.events")
3. the desktop app writes each card's text; a web page draws them beside the orb
"Sign in with Google" card → your click → a request to the voice agent → it opens your browser
```
- Clicks go through the cards except on a link or button; they never take your keyboard.
- One card per kind; they fade 10 s after the conversation; "clear the cards" or "stop" clear them; hiding the orb
  hides them (a sign-in card still shows).
- Text chat prints the further-reading links under his reply; no other cards there.

## The menu bar and Jarvis.app: running in the background
```
at login: launchd → ~/Applications/Jarvis.app (launcher: Python as a child) → the desktop app → the menu-bar icon
"Start Jarvis" / ⌃⌥J → the crew: LiveKit server → voice agent → the orb shows → the room client joins with your mic
"Stop Jarvis" → leave the room → hide the orb → stop the agent → stop the server       (the mic is released)
⌃⌥M → the mic track is muted: the Mac stops recording · ⌃⌥J while he talks → RPC jarvis.talk → he stops, listens
```
- macOS gives the mic to the app at the top of the process tree: Jarvis.app, not your terminal; asked once.
- Start Options: launch at login (the icon, and he starts), show in the menu bar at login (the default), or neither.
- The menu also shows how he is (a coloured dot), hides / shows the orb, picks its centrepiece, connects or
  disconnects Google and Notion, and quits.

## Config
- `.env`: secrets, gitignored · `.env.example`: template, committed. Includes Jarvis's Google sign-in app (`GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`) and the LiveKit server (`LIVEKIT_URL` / `_API_KEY` / `_API_SECRET`; the defaults are the local dev server's fixed, public ones)
- macOS Keychain: your Google and Notion tokens (entries `jarvis` / `google`, `jarvis` / `notion`), never in a file
- `jarvis.toml`: preferences (models, `[location] home_city`, `[memory] new_day_hour`, `[auth] remember_days`, `[files] folders`, `[notion] open_pages`, `[voice] stt_model / tts_model / fallback_voice / follow_up_seconds`), committed
- Google Cloud (Jarvis's Google app): Calendar API + Tasks API switched on; sign-in scopes: calendar events, your calendar list, tasks
- `data/jarvis.db`: memory (today's conversation + facts), gitignored · `data/orb.json`: where the orb is and its centrepiece · `data/app.json`: whether Jarvis starts as the app opens
- `~/Applications/Jarvis.app` (bundle id `io.github.bspowell.jarvis`: its mic permission) and `~/Library/LaunchAgents/io.github.bspowell.jarvis.plist` (open at login), both written by `jarvis install`
- Priority: env vars > `.env` > `jarvis.toml` > code defaults
- `SecretStr` hides keys in logs; `jarvis doctor` shows only set/missing

## Tooling
- `uv`: Python + packages + lock file; `uv run <cmd>` runs inside the project
- `ruff`: lint + format · `pytest`: tests · `gh`: GitHub
- `langgraph dev`: LangGraph Studio, a visual debugger for the agent graph
- `jarvis up`: the desktop app, starting Jarvis at once (LiveKit server + voice agent), colour-tagged output; Ctrl+C (or closing the window) stops them in reverse
- `jarvis install` / `uninstall`: Jarvis.app + Open at Login (after editing desktop code: Quit Jarvis, reopen the app; agent code reloads on each Start)
- `lk agent console`: the voice agent alone, with your mic + speakers in the terminal · `lk agent debugger`: typed turns, no audio, prints replies + timings
- `jarvis desktop --demo`: the cards, then the orb's every look and centrepiece in turn, no Jarvis needed · `src/jarvis/desktop/web/orb.html#demo` and `cards.html#demo` in a browser: try looks, centrepieces, cards and backgrounds by hand
- `livekit-server --dev`: the local LiveKit server (Homebrew `livekit`); `jarvis up` starts it unless one is running
