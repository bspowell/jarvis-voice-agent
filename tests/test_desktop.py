"""The desktop app: the line to Jarvis on its own thread, where the orb's window goes, and what
it remembers between launches."""

import asyncio
import threading

from jarvis.desktop.app import Line
from jarvis.desktop.card_panel import TUCK, WIDTH, beside, spot_under
from jarvis.desktop.overlay import MARGIN, Remembered, corner_origin


def test_the_line_runs_on_its_own_thread_until_hung_up():
    started, over = threading.Event(), threading.Event()
    threads: list[str] = []

    async def call() -> None:
        threads.append(threading.current_thread().name)
        started.set()
        await asyncio.sleep(3600)

    line = Line(call(), when_over=over.set)
    line.start()
    assert started.wait(2)
    line.hang_up()
    assert over.wait(2)  # the app hears the line has ended (it then closes the window)
    line.wait(2)
    assert threads == ["line-to-jarvis"]
    assert not line.thread.is_alive()
    line.hang_up()  # hanging up twice is harmless


def test_a_line_that_breaks_still_tells_the_app():
    over = threading.Event()

    async def call() -> None:
        raise RuntimeError("the line broke")

    line = Line(call(), when_over=over.set)
    line.start()
    assert over.wait(2)


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
