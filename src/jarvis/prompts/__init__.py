"""System prompts live as Markdown files next to this module, so they're easy to read and edit."""

from functools import cache
from importlib.resources import files


@cache
def load_prompt(name: str) -> str:
    """Return the prompt in `<name>.md`, e.g. `load_prompt("jarvis")`."""
    return files(__package__).joinpath(f"{name}.md").read_text(encoding="utf-8").strip()
