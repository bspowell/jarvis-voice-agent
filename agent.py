"""Jarvis's voice agent, where LiveKit's CLI looks for it: `lk agent console` / `debugger`.

The agent itself lives in src/jarvis/voice/ (see worker.py).
"""

from livekit.agents import cli

from jarvis.voice.worker import server

if __name__ == "__main__":
    cli.run_app(server)
