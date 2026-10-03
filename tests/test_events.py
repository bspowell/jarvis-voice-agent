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
