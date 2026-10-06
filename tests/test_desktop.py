"""The desktop app: its second thread (the line), one copy at a time, where the orb's window
goes, and what it remembers between launches."""

import asyncio
import threading

from jarvis.desktop.app import hold_lock
from jarvis.desktop.card_panel import TUCK, WIDTH, beside, spot_under
from jarvis.desktop.line import Line
from jarvis.desktop.overlay import MARGIN, Remembered, corner_origin


def test_the_line_runs_work_on_its_own_thread_until_closed():
    line = Line()
    line.start()
    threads: list[str] = []

    async def work() -> str:
        threads.append(threading.current_thread().name)
        await asyncio.sleep(0)
        return "done"

    assert line.submit(work()).result(timeout=2) == "done"
    called = threading.Event()
    line.call(called.set)  # a plain function, from this (another) thread
    assert called.wait(2)
    line.close()
    assert threads == ["line-to-jarvis"]
    assert not line.thread.is_alive()
    line.call(called.set)  # after closing: ignored, no error


def test_only_one_desktop_app_runs_at_a_time(tmp_path):
    first = hold_lock(tmp_path / "desktop.lock")
    assert first is not None
    assert hold_lock(tmp_path / "desktop.lock") is None  # a second app would hear you too
    first.close()  # the first one quits
    again = hold_lock(tmp_path / "desktop.lock")
    assert again is not None
    again.close()


def test_each_corner_keeps_clear_of_the_screen_edges():
    screen = (0, 0, 1352, 848)  # x, y, width, height; macOS counts y up from the bottom
    assert corner_origin(screen, 280, "bottom right") == (1352 - 280 - MARGIN, MARGIN)
    assert corner_origin(screen, 280, "top left") == (MARGIN, 848 - 280 - MARGIN)
    second = (1352, 200, 2560, 1415)  # a second screen, to the right and higher up
    assert corner_origin(second, 280, "bottom left") == (1352 + MARGIN, 200 + MARGIN)


def test_the_orb_remembers_where_it_was_and_its_centrepiece(tmp_path):
    memory = tmp_path / "orb.json"
    assert Remembered.load(memory) == Remembered()  # first launch: bottom right, classic rings
    Remembered("top left", "LG UltraFine", "mjolnir").save(memory)
    assert Remembered.load(memory) == Remembered("top left", "LG UltraFine", "mjolnir")


def test_a_damaged_memory_falls_back_to_the_defaults(tmp_path):
    memory = tmp_path / "orb.json"
    memory.write_text("{not json")
    assert Remembered.load(memory) == Remembered()
    memory.write_text('{"corner": "middle", "core": "shield", "screen": 3}')
    assert Remembered.load(memory) == Remembered()


def test_the_cards_sit_beside_the_orb_lined_up_with_it():
    screen = (0, 0, 1352, 848)  # x, y, width, height (y counts up from the bottom)
    orb = (1060, 12, 280, 280)  # bottom right
    assert beside(orb, "bottom right", screen) == (1060 - WIDTH + TUCK, 12, WIDTH, 848 - 2 * MARGIN)
    top_left = (12, 556, 280, 280)
    x, y, _, height = beside(top_left, "top left", screen)
    assert x == 12 + 280 - TUCK  # to its right
    assert y + height == 556 + 280  # its top lines up with the orb's top


def test_only_links_and_buttons_take_clicks():
    spots = [{"id": "sources:0", "x": 25, "y": 289, "w": 270, "h": 51}]
    assert spot_under(spots, 100, 300) == "sources:0"
    assert spot_under(spots, 100, 200) is None  # the card's text: clicks go through
    assert spot_under([], 100, 300) is None
