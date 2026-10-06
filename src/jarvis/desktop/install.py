"""Jarvis.app and "Open at Login" (`jarvis install` / `jarvis uninstall`).

macOS gives the mic to an *app*, and decides which one by what started the process: in a
terminal, the terminal app; at login, nothing at all. So `~/Applications/Jarvis.app` is a tiny
app whose program (launcher.c, compiled here with clang) opens the desktop app as its child and
stays running: macOS asks once, "“Jarvis” would like to access the microphone", and remembers.

"Open at Login" is a LaunchAgent: a small file in ~/Library/LaunchAgents that tells launchd
(macOS's starter of programs) to open Jarvis.app when you log in, and again if it crashes, but
not after you quit it.
"""

import contextlib
import filecmp
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
from importlib.metadata import version
from pathlib import Path
from typing import Any

from jarvis.config import PROJECT_ROOT, Settings

BUNDLE_ID = "io.github.bspowell.jarvis"
APP = Path.home() / "Applications" / "Jarvis.app"  # yours: no admin password needed
LAUNCH_AGENT = Path.home() / "Library" / "LaunchAgents" / f"{BUNDLE_ID}.plist"
LAUNCHER = Path(__file__).with_name("launcher.c")
PROGRAM = Path("Contents") / "MacOS" / "Jarvis"  # inside the app
APP_LOG = Path("logs") / "app.log"  # in data/: what the app prints outside up.log (crashes)
MIC_REASON = (
    "Jarvis listens for “Hey Jarvis” on your Mac. Your voice leaves it only after he wakes."
)
SYSTEM_PATH = ["/usr/bin", "/bin", "/usr/sbin", "/sbin"]


class InstallFailed(Exception):
    """Jarvis.app couldn't be built or opened."""


def info_plist(app_version: str) -> dict[str, Any]:
    """What macOS reads about the app: its name, its program, why it wants the mic."""
    return {
        "CFBundleIdentifier": BUNDLE_ID,  # how macOS remembers its mic permission
        "CFBundleName": "Jarvis",
        "CFBundleDisplayName": "Jarvis",
        "CFBundleExecutable": PROGRAM.name,
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": app_version,
        "CFBundleVersion": app_version,
        "LSMinimumSystemVersion": "13.0",
        # No Dock icon and no window of its own: the desktop app it opens has the menu-bar icon.
        "LSBackgroundOnly": True,
        "NSMicrophoneUsageDescription": MIC_REASON,  # shown when macOS asks for the mic
    }


def launch_agent(app: Path) -> dict[str, Any]:
    """The LaunchAgent: open Jarvis.app at login; again after a crash, not after you quit."""
    return {
        "Label": BUNDLE_ID,
        "ProgramArguments": [str(app / PROGRAM)],
        "RunAtLoad": True,  # at login
        "KeepAlive": {"SuccessfulExit": False},  # after a crash (Quit exits with 0)
        # Without it, launchd throttles the app's CPU and disk as background work: live audio
        # needs them on time.
        "ProcessType": "Interactive",
        "LimitLoadToSessionType": "Aqua",  # only when you log in to the desktop (not over SSH)
        "AssociatedBundleIdentifiers": [BUNDLE_ID],  # listed as Jarvis in Login Items
        "ExitTimeOut": 30,  # seconds to stop Jarvis tidily at log out, before launchd kills
    }


def search_path() -> str:
    """Where Jarvis.app finds programs (launchd's own PATH lacks Homebrew's livekit-server)."""
    found = [Path(found).parent for name in ("livekit-server",) if (found := shutil.which(name))]
    folders = [str(folder) for folder in found] + ["/opt/homebrew/bin", "/usr/local/bin"]
    return os.pathsep.join(dict.fromkeys(folders + SYSTEM_PATH))  # in order, each once


def c_string(text: str) -> str:
    """A C string literal, for clang's -D."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def build_app(app: Path, python: Path, project: Path, log: Path, app_version: str) -> None:
    """Build Jarvis.app at `app`: its Info.plist, its program (compiled), signed for this Mac."""
    program = app / PROGRAM
    program.parent.mkdir(parents=True)
    (app / "Contents" / "Info.plist").write_bytes(plistlib.dumps(info_plist(app_version)))
    run(
        "clang",
        "-Os",
        "-Wall",
        f"-DPYTHON={c_string(str(python))}",
        f"-DPROJECT={c_string(str(project))}",
        f"-DSEARCH_PATH={c_string(search_path())}",
        f"-DLOG={c_string(str(log))}",
        "-o",
        str(program),
        str(LAUNCHER),
    )
    # Ad hoc: signed for this Mac only (no developer account). macOS ties the mic permission to
    # this signature, so an unchanged app keeps it (`install` doesn't rebuild what's the same).
    run("codesign", "--force", "--sign", "-", "--identifier", BUNDLE_ID, str(app))


def same_app(new: Path, old: Path) -> bool:
    """Is the installed app identical to the one just built? Then keep it (and its permission)."""
    parts = [Path("Contents") / "Info.plist", PROGRAM]
    return old.exists() and all(
        (old / part).exists() and filecmp.cmp(new / part, old / part, shallow=False)
        for part in parts
    )


def install(settings: Settings) -> list[str]:
    """Build Jarvis.app (if it changed), turn on Open at Login and open it now. Returns what was
    done, in words."""
    if sys.platform != "darwin":
        raise InstallFailed("Jarvis.app is for macOS.")
    if shutil.which("clang") is None:
        raise InstallFailed("clang is missing: install Xcode's command line tools.")
    log = settings.data_dir / APP_LOG
    log.parent.mkdir(parents=True, exist_ok=True)
    said = []
    with tempfile.TemporaryDirectory() as folder:
        built = Path(folder) / APP.name
        build_app(built, Path(sys.executable), PROJECT_ROOT, log, version("jarvis"))
        if same_app(built, APP):
            said.append(f"{APP} is up to date.")
        else:
            APP.parent.mkdir(parents=True, exist_ok=True)
            shutil.rmtree(APP, ignore_errors=True)
            shutil.move(built, APP)
            said.append(f"Built {APP}.")
    set_open_at_login(True)
    said.append("It opens when you log in (untick Open at Login in its menu to stop that).")
    said.append(open_now())
    return said


def open_now() -> str:
    """Open Jarvis.app through launchd, as at login (it shows the menu-bar icon)."""
    domain = f"gui/{os.getuid()}"
    loaded = launchctl("print", f"{domain}/{BUNDLE_ID}").returncode == 0
    if not loaded:
        launchctl("bootstrap", domain, str(LAUNCH_AGENT))  # loads it, and RunAtLoad opens it
    else:
        launchctl("kickstart", f"{domain}/{BUNDLE_ID}")  # opens it, unless it's running
    return "Jarvis's icon is in the menu bar (if `jarvis up` is running, quit that first)."


def uninstall() -> list[str]:
    """Quit Jarvis.app, remove it and its LaunchAgent, and forget its mic permission."""
    said = []
    launchctl("bootout", f"gui/{os.getuid()}/{BUNDLE_ID}")  # quits it, if launchd opened it
    if LAUNCH_AGENT.exists():
        LAUNCH_AGENT.unlink()
        said.append("Jarvis no longer opens at login.")
    if APP.exists():
        shutil.rmtree(APP)
        said.append(f"Removed {APP}.")
    with contextlib.suppress(OSError):
        subprocess.run(["tccutil", "reset", "Microphone", BUNDLE_ID], capture_output=True)
    return said or ["Jarvis.app wasn't installed."]


def opens_at_login() -> bool | None:
    """Does Jarvis.app open at login? None: it isn't installed."""
    return LAUNCH_AGENT.exists() if APP.exists() else None


def set_open_at_login(on: bool) -> None:
    """Write or remove the LaunchAgent. It takes effect at your next login; turning it off
    leaves the app running now."""
    if on:
        LAUNCH_AGENT.parent.mkdir(parents=True, exist_ok=True)
        LAUNCH_AGENT.write_bytes(plistlib.dumps(launch_agent(APP)))
    else:
        LAUNCH_AGENT.unlink(missing_ok=True)


def launchctl(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True)


def run(*command: str) -> None:
    done = subprocess.run(command, capture_output=True, text=True)
    if done.returncode != 0:
        raise InstallFailed(f"{command[0]} failed: {done.stderr.strip() or done.stdout.strip()}")
