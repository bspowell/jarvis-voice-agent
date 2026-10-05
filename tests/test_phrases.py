from collections.abc import AsyncIterator

from fakes import FakeVoice

from jarvis.voice.phrases import Phrases, fixed_line

FILLER = "Right away, sir."
GREETING = "Good evening, sir. Three Agents all present and accounted for."


async def stream(*chunks: str, pulled: list[str] | None = None) -> AsyncIterator[str]:
    """Text arriving chunk by chunk, like a reply being written. `pulled` records what was read."""
    for chunk in chunks:
        if pulled is not None:
            pulled.append(chunk)
        yield chunk


async def text_of(chunks: AsyncIterator[str]) -> str:
    return "".join([chunk async for chunk in chunks])


async def test_a_line_is_recorded_once_then_replayed(tmp_path):
    voice = FakeVoice()
    phrases = Phrases(tmp_path, voice, voice_name="jarvis/eleven_v4_turbo")
    await phrases.record({FILLER})
    await phrases.record({FILLER})  # already recorded: no second request
    assert voice.lines == [FILLER]
    assert phrases.recorded({FILLER, GREETING}) == {FILLER}
    frames = [frame async for frame in phrases.play(FILLER)]
    assert sum(frame.samples_per_channel for frame in frames) == 720  # all 30 ms came back
    assert frames[0].sample_rate == FakeVoice.SAMPLE_RATE


async def test_another_voice_records_its_own_copy(tmp_path):
    await Phrases(tmp_path, FakeVoice(), voice_name="jarvis/eleven_v4_turbo").record({FILLER})
    other = Phrases(tmp_path, FakeVoice(), voice_name="jarvis/eleven_flash_v2_5")
    assert other.recorded({FILLER}) == set()  # a new model or voice never plays an old recording


async def test_a_failed_recording_leaves_nothing_behind(tmp_path):
    phrases = Phrases(tmp_path, FakeVoice(fail=True), voice_name="jarvis")
    await phrases.record({FILLER})  # logged, not raised: the line is simply spoken live
    assert phrases.recorded({FILLER}) == set()
    assert list(tmp_path.iterdir()) == []


async def test_a_whole_fixed_line_is_recognised_however_it_is_split():
    line, rest = await fixed_line(stream("Right ", "away, ", "sir. "), {FILLER})
    assert line == FILLER
    assert await text_of(rest) == ""


async def test_a_reply_that_merely_starts_with_a_fixed_line_is_spoken_whole():
    chunks = ("Right away, sir. ", "Your calendar is clear.")
    line, rest = await fixed_line(stream(*chunks), {FILLER})
    assert line is None
    assert await text_of(rest) == "".join(chunks)


async def test_an_ordinary_reply_is_not_held_back():
    pulled: list[str] = []
    chunks = ("Rain ", "at four, ", "sir.")
    line, rest = await fixed_line(stream(*chunks, pulled=pulled), {FILLER, GREETING})
    assert line is None
    assert pulled == ["Rain "]  # one chunk was enough to know: the voice starts straight away
    assert await text_of(rest) == "".join(chunks)
