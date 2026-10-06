import pytest

from jarvis.events import Event, EventBus


def test_every_subscriber_hears_every_event_in_order():
    bus = EventBus()
    chat, voice = [], []
    bus.subscribe(chat.append)
    bus.subscribe(voice.append)

    started = Event("agent_started", {"agent": "search"})
    bus.publish(started)
    bus.publish(Event("agent_started", {"agent": "calendar"}))

    assert chat == voice == [started, Event("agent_started", {"agent": "calendar"})]


def test_publishing_with_no_subscribers_is_fine():
    EventBus().publish(Event("agent_started"))


def test_events_travel_as_text_and_come_back_the_same():
    weather = Event("weather", {"place": "Vancouver", "now": {"temp_c": 14}})
    assert Event.from_json(weather.to_json()) == weather
    assert Event.from_json('{"name": "agent_started"}') == Event("agent_started")


@pytest.mark.parametrize(
    "text", ["not json", "[1, 2]", '{"details": {}}', '{"name": "x", "details": 3}']
)
def test_anything_else_is_refused(text):
    with pytest.raises(ValueError):
        Event.from_json(text)
