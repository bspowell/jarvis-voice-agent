"""File tools for the Notes agent: find, list, read, create and add to files, in your folders only.

The sandbox is enforced in code on every call, whatever the model asks for:
- Only inside the folders listed in jarvis.toml (`[files] folders`).
- A path is resolved first (`..` and symlinks followed), then checked, so nothing escapes.
- Hidden files and folders (`.env`, `.ssh`, `.git`…) and secret-looking files: never.
- Writes never replace or remove anything: `create_file` makes a new note and `add_to_file`
  appends to one. Both pause for your OK (`agents/notes.py`); `describe_*` say exactly what.
"""

import asyncio
import os
import re
import subprocess
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from langchain.agents.middleware import AgentState
from langchain.tools import tool
from langchain_core.messages import ToolCall
from langgraph.runtime import Runtime

from jarvis.config import get_settings

NOTES = {".md", ".markdown", ".txt"}  # searched inside, and the only kinds Jarvis writes
DOCUMENTS = {".docx", ".doc", ".rtf", ".odt"}  # read through macOS's `textutil`
BULKY_FOLDERS = {"node_modules", "__pycache__", "venv", "build", "dist", "target", "Pods"}
SECRET_LOOKING = re.compile(
    r"secret|passw|credential|private.?key|id_(rsa|dsa|ecdsa|ed25519)|\.env\b"
    r"|\.(pem|key|p12|pfx|kdbx|keychain|gpg|asc)$",
    re.IGNORECASE,
)
MAX_MATCHES = 10
MAX_LISTED = 50
MAX_READ_CHARS = 20_000  # about 5k tokens: a whole note, or the start of a long file
MAX_SEARCHED_BYTES = 1_000_000  # bigger "notes" are logs or exports: not worth reading through


class Refused(Exception):
    """A file request the tools turn down, with the reason the model hears."""


@dataclass(frozen=True)
class Sandbox:
    """Jarvis's folders, and the rules for everything inside them."""

    folders: tuple[Path, ...]  # resolved, so a symlinked folder compares correctly

    @classmethod
    def of(cls, folders: Sequence[str | Path]) -> "Sandbox":
        return cls(tuple(Path(folder).expanduser().resolve() for folder in folders))

    def check(self, path: str | Path) -> Path:
        """The real path, if Jarvis may use it; raises Refused with the reason if not."""
        given = Path(path).expanduser()
        if not given.is_absolute():
            given = Path.home() / given  # "Desktop/notes.md" means ~/Desktop/notes.md
        real = given.resolve()  # follows `..` and symlinks: check where it really points
        root = next((folder for folder in self.folders if real.is_relative_to(folder)), None)
        if root is None:
            raise Refused(f"{shown(given)} is outside Jarvis's folders ({self.names()}).")
        inside = real.relative_to(root).parts
        if any(part.startswith(".") for part in inside):
            raise Refused(f"{shown(given)} is hidden: hidden files and folders are off-limits.")
        if any(SECRET_LOOKING.search(part) for part in inside):
            raise Refused(f"{shown(given)} looks like it holds secrets: off-limits.")
        return real

    def check_note(self, path: str | Path) -> Path:
        """Like `check`, for writing: only notes (.md, .txt) are ever written."""
        real = self.check(path)
        if real.suffix.lower() not in NOTES:
            raise Refused("Jarvis only writes notes: files ending in .md or .txt.")
        return real

    def names(self) -> str:
        return ", ".join(shown(folder) for folder in self.folders)

    # --- Reading ---
    def find(self, words: str, folder: str | None = None) -> list[dict[str, Any]]:
        """Files and folders named with every word, and notes whose text has them; newest first."""
        wanted = words.lower().split()
        if not wanted:
            raise Refused("Give at least one word to look for.")
        roots = [self.check(folder)] if folder else list(self.folders)
        found = [
            path
            for path in walk(roots)
            if all(word in path.name.lower() for word in wanted)
            or (path.suffix.lower() in NOTES and mentions(path, wanted))
        ]
        found.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        return [summary(path) for path in found[:MAX_MATCHES]]

    def contents(self, folder: str) -> dict[str, Any]:
        real = self.check(folder)
        if not real.is_dir():
            raise Refused(f"There's no folder at {shown(real)}.")
        entries = sorted(
            (entry for entry in real.iterdir() if visible(entry.name) and not entry.is_symlink()),
            key=lambda entry: entry.stat().st_mtime,
            reverse=True,
        )
        return {
            "folder": shown(real),
            "entries": [summary(entry) for entry in entries[:MAX_LISTED]],
            "more": max(0, len(entries) - MAX_LISTED),
        }

    def read(self, path: str) -> dict[str, Any]:
        real = self.check(path)
        if not real.is_file():
            raise Refused(f"There's no file at {shown(real)}.")
        text = document_text(real) if real.suffix.lower() in DOCUMENTS else plain_text(real)
        return {
            "path": shown(real),
            "text": text[:MAX_READ_CHARS],
            "cut_short": len(text) > MAX_READ_CHARS,
        }

    # --- Writing (runs only after you approve) ---
    def create(self, path: str, content: str) -> dict[str, Any]:
        real = self.check_note(path)
        if real.exists():
            raise Refused(
                f"{shown(real)} already exists, and Jarvis never replaces files: "
                "pick another name, or add to it instead."
            )
        if not real.parent.is_dir():
            raise Refused(f"There's no folder at {shown(real.parent)}.")
        with real.open("x", encoding="utf-8") as file:  # "x": never overwrites, even in a race
            file.write(as_lines(content))
        return {"created": shown(real), "lines": len(content.splitlines())}

    def add(self, path: str, text: str) -> dict[str, Any]:
        real = self.check_note(path)
        if not real.is_file():
            raise Refused(f"There's no file at {shown(real)}: create it instead.")
        new_line = "\n" if ends_mid_line(real) else ""  # never glue the addition onto a last line
        with real.open("a", encoding="utf-8") as file:
            file.write(new_line + as_lines(text))
        return {"added_to": shown(real), "lines": len(text.splitlines())}


def walk(roots: Sequence[Path]) -> Iterator[Path]:
    """Every visible file and folder under `roots`. Skips hidden, secret and bulky code folders,
    and symlinks (they could point anywhere)."""
    for root in roots:
        for folder, subfolders, files in os.walk(root):  # doesn't follow symlinked folders
            subfolders[:] = [
                name
                for name in subfolders
                if visible(name)
                and name not in BULKY_FOLDERS
                and not os.path.islink(os.path.join(folder, name))
            ]
            for name in [*subfolders, *files]:
                path = Path(folder, name)
                if visible(name) and not path.is_symlink():
                    yield path


def visible(name: str) -> bool:
    return not name.startswith(".") and not SECRET_LOOKING.search(name)


def mentions(path: Path, words: Sequence[str]) -> bool:
    try:
        with path.open("rb") as file:
            text = file.read(MAX_SEARCHED_BYTES).decode("utf-8", errors="ignore").lower()
    except OSError:  # unreadable (permissions): skip it
        return False
    return all(word in text for word in words)


def plain_text(path: Path) -> str:
    with path.open("rb") as file:
        raw = file.read(MAX_READ_CHARS * 4 + 1)  # enough bytes for the most text we return
    if b"\0" in raw[:8192]:  # binary: an image, a PDF, an app…
        raise Refused(f"{shown(path)} isn't text Jarvis can read (notes, text and Word files).")
    return raw.decode("utf-8", errors="replace")


def document_text(path: Path) -> str:
    """Word and rich-text documents, converted to plain text by macOS's built-in `textutil`."""
    try:
        converted = subprocess.run(
            ["textutil", "-convert", "txt", "-stdout", str(path)],
            capture_output=True,
            timeout=10,
            check=True,
        )
    except (subprocess.SubprocessError, OSError) as error:
        raise Refused(f"Couldn't read {shown(path)}.") from error
    return converted.stdout.decode("utf-8", errors="replace")


def ends_mid_line(path: Path) -> bool:
    """True if the file's last line has no line break yet."""
    with path.open("rb") as file:
        if file.seek(0, os.SEEK_END) == 0:
            return False  # empty
        file.seek(-1, os.SEEK_END)
        return file.read(1) != b"\n"


def as_lines(text: str) -> str:
    return text if text.endswith("\n") else text + "\n"


def summary(path: Path) -> dict[str, Any]:
    if path.is_dir():
        kind = "folder"
    elif path.suffix.lower() in NOTES:
        kind = "note"
    else:
        kind = "file"
    changed = datetime.fromtimestamp(path.stat().st_mtime)
    return {
        "path": shown(path),
        "kind": kind,
        "modified": f"{changed:%a} {changed.day} {changed:%b %Y}",
    }


def shown(path: Path) -> str:
    """The path as people write it: "~/Desktop/notes.md"."""
    try:
        return f"~/{path.relative_to(Path.home())}"
    except ValueError:
        return str(path)


# --- Tools (what the Notes agent's model sees) ---
def sandbox() -> Sandbox:
    return Sandbox.of(get_settings().files.folders)


async def attempt(action: Callable[[Sandbox], dict[str, Any]]) -> dict[str, Any]:
    """Run a file action on a thread (disks can be slow); refusals become a note for the model."""
    try:
        return await asyncio.to_thread(action, sandbox())
    except Refused as problem:
        return {"error": str(problem)}
    except OSError as error:  # e.g. macOS hasn't given this app access to that folder
        return {"error": f"The file couldn't be used: {error.strerror or error}."}


@tool(parse_docstring=True)
async def find_files(words: str, folder: str | None = None) -> dict[str, Any]:
    """Find the user's files and folders by name, and their notes (.md, .txt) by what they say.

    Args:
        words: A few key words, e.g. "trek trip". Every word must match.
        folder: Only look inside this folder, e.g. "~/Documents/Taxes". Leave out to look
            everywhere.
    """
    return await attempt(lambda box: {"matches": box.find(words, folder)})


@tool(parse_docstring=True)
async def list_folder(folder: str) -> dict[str, Any]:
    """List what's in one of the user's folders, newest first.

    Args:
        folder: The folder, e.g. "~/Desktop".
    """
    return await attempt(lambda box: box.contents(folder))


@tool(parse_docstring=True)
async def read_file(path: str) -> dict[str, Any]:
    """Read a text file, note or Word document. Very long files arrive cut short.

    Args:
        path: The file, e.g. "~/Documents/ideas.md", as find_files or list_folder showed it.
    """
    return await attempt(lambda box: box.read(path))


@tool(parse_docstring=True)
async def create_file(path: str, content: str) -> dict[str, Any]:
    """Save a new note as a file. Never replaces a file. The user confirms before it's saved.

    Args:
        path: The new file, ending in .md or .txt, e.g. "~/Desktop/groceries.md".
        content: What the note says, in plain text or simple Markdown.
    """
    return await attempt(lambda box: box.create(path, content))


@tool(parse_docstring=True)
async def add_to_file(path: str, text: str) -> dict[str, Any]:
    """Add text to the end of an existing note (.md or .txt). The user confirms first.

    Args:
        path: The note, as find_files or list_folder showed it.
        text: What to add, e.g. "- milk".
    """
    return await attempt(lambda box: box.add(path, text))


# --- Exact words for approvals (built by code from the arguments, so they can't drift) ---
def describe_new_file(tool_call: ToolCall, state: AgentState, runtime: Runtime) -> str:
    args = tool_call["args"]
    return f"Create the file {args.get('path', '?')}, saying: {preview(args.get('content', ''))}"


def describe_file_addition(tool_call: ToolCall, state: AgentState, runtime: Runtime) -> str:
    args = tool_call["args"]
    return f"Add to the end of the file {args.get('path', '?')}: {preview(args.get('text', ''))}"


def preview(text: str, limit: int = 160) -> str:
    """The start of what would be written, on one line."""
    flat = " / ".join(line.strip() for line in text.strip().splitlines() if line.strip())
    return f"“{flat[:limit]}…”" if len(flat) > limit else f"“{flat}”"
