"""Jarvis.app and Open at Login: what macOS reads, and the launcher itself, compiled and run with
a stand-in for Python (so no Jarvis starts)."""

import os
import plistlib
import shutil
import signal
import subprocess
import sys
import time

import pytest

from jarvis.desktop import install
from jarvis.desktop.install import BUNDLE_ID, PROGRAM, build_app, c_string, info_plist, same_app

needs_clang = pytest.mark.skipif(
    sys.platform != "darwin" or shutil.which("clang") is None, reason="builds a macOS app"
)

# The stand-in for Python: notes how it was started, then exits 3, or waits to be stopped.
STAND_IN = """#!/bin/sh
echo "args: $*" > "$NOTES"
echo "folder: $(pwd)" >> "$NOTES"
echo "path: $PATH" >> "$NOTES"
echo "said to stdout"
echo "said to stderr" >&2
if [ "$WAIT" = 1 ]; then
    trap 'echo "stopped tidily" >> "$NOTES"; exit 0' TERM
    while true; do sleep 0.1; done
fi
exit 3
"""


def test_the_app_says_why_it_wants_the_mic():
    plist = info_plist("0.1.0")
    assert plist["CFBundleIdentifier"] == BUNDLE_ID  # how macOS remembers its permission
    assert "Hey Jarvis" in plist["NSMicrophoneUsageDescription"]
    assert plist["LSBackgroundOnly"] is True  # no Dock icon: the desktop app has the menu bar


def test_the_launch_agent_opens_it_at_login_and_after_a_crash_but_not_after_quit(tmp_path):
    agent = install.launch_agent(tmp_path / "Jarvis.app")
    assert agent["ProgramArguments"] == [str(tmp_path / "Jarvis.app" / PROGRAM)]
    assert agent["RunAtLoad"] is True
    assert agent["KeepAlive"] == {"SuccessfulExit": False}  # Quit exits with 0: stays quit
    assert agent["ProcessType"] == "Interactive"  # no background throttling: live audio


def test_paths_are_written_into_c_safely():
    assert c_string('/a "b"\\c') == '"/a \\"b\\"\\\\c"'


@pytest.fixture
def built(tmp_path):
    """Jarvis.app, built in a temporary folder around the stand-in for Python."""
    python = tmp_path / "python"
    python.write_text(STAND_IN)
    python.chmod(0o755)
    project = tmp_path / "project with a space"
    project.mkdir()
    app = tmp_path / "Jarvis.app"
    build_app(app, python, project, tmp_path / "app.log", "0.1.0")
    return app


@needs_clang
def test_the_launcher_opens_the_desktop_app_from_the_project(built, tmp_path):
    notes = tmp_path / "notes"
    done = subprocess.run(
        [str(built / PROGRAM)], env={"NOTES": str(notes), "PATH": "/usr/bin:/bin"}, timeout=10
    )
    assert done.returncode == 3  # the desktop app's exit code is the launcher's
    said = notes.read_text().splitlines()
    assert said[0] == "args: -m jarvis desktop"
    assert said[1] == f"folder: {tmp_path / 'project with a space'}"
    assert "/opt/homebrew/bin" in said[2]  # launchd's PATH lacks Homebrew: the launcher adds it
    log = (tmp_path / "app.log").read_text()  # what it printed goes to the app's log
    assert "said to stdout" in log
    assert "said to stderr" in log


@needs_clang
def test_quitting_the_launcher_stops_the_desktop_app_tidily(built, tmp_path):
    notes = tmp_path / "notes"
    launcher = subprocess.Popen(
        [str(built / PROGRAM)], env={"NOTES": str(notes), "WAIT": "1", "PATH": "/usr/bin:/bin"}
    )
    deadline = time.monotonic() + 10
    while not notes.exists():
        assert time.monotonic() < deadline, "the stand-in never started"
        time.sleep(0.05)
    time.sleep(0.2)  # its trap is set
    launcher.send_signal(signal.SIGTERM)  # what launchd sends at log out
    assert launcher.wait(10) == 0
    assert notes.read_text().splitlines()[-1] == "stopped tidily"


@needs_clang
def test_the_app_is_signed_and_the_same_build_twice(built, tmp_path):
    verified = subprocess.run(["codesign", "--verify", "--strict", str(built)], capture_output=True)
    assert verified.returncode == 0, verified.stderr
    details = subprocess.run(["codesign", "-dv", str(built)], capture_output=True, text=True)
    assert f"Identifier={BUNDLE_ID}" in details.stderr
    again = tmp_path / "again" / "Jarvis.app"
    build_app(
        again, tmp_path / "python", tmp_path / "project with a space", tmp_path / "app.log", "0.1.0"
    )
    # Identical, so reinstalling keeps the app macOS gave the mic to.
    assert same_app(again, built)


def test_open_at_login_writes_and_removes_the_launch_agent(tmp_path, monkeypatch):
    monkeypatch.setattr(install, "APP", tmp_path / "Jarvis.app")
    monkeypatch.setattr(install, "LAUNCH_AGENT", tmp_path / "agents" / f"{BUNDLE_ID}.plist")
    assert install.opens_at_login() is None  # not installed
    (tmp_path / "Jarvis.app").mkdir()
    assert install.opens_at_login() is False
    install.set_open_at_login(True)
    assert install.opens_at_login() is True
    written = plistlib.loads((tmp_path / "agents" / f"{BUNDLE_ID}.plist").read_bytes())
    assert written["Label"] == BUNDLE_ID
    install.set_open_at_login(False)
    assert install.opens_at_login() is False
    assert os.listdir(tmp_path / "agents") == []
