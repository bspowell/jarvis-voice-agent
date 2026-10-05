from datetime import datetime

from fakes import FakeApprovalModel
from langgraph.types import Interrupt

from jarvis.brain.approvals import Approvals, Question, Reading, decision_for

LUNCH = "Add “Lunch with Sam” to your calendar: Mon 5 Oct 2026, 12 PM to 1 PM."
PAUSED = Interrupt(
    value={
        "action_requests": [{"name": "create_event", "args": {}, "description": LUNCH}],
        "review_configs": [
            {"action_name": "create_event", "allowed_decisions": ["approve", "reject"]}
        ],
    },
    id="pause-1",
)


async def test_the_question_is_built_from_the_exact_description():
    model = FakeApprovalModel(messages=iter(["Shall I book lunch with Sam for Monday at noon?"]))
    header = "[Sunday 4 October 2026, 3:15 PM PDT] Waiting for approval:"
    question = "".join([words async for words in Approvals(model).ask([PAUSED], header)])

    assert question == "Shall I book lunch with Sam for Monday at noon?"
    sent = model.seen[0][-1].text
    assert sent.startswith("[Sunday 4 October 2026")  # so "tomorrow" is only said when true
    assert LUNCH in sent


async def test_your_answer_is_read_by_the_model_not_by_keywords():
    model = FakeApprovalModel(
        messages=iter([]), readings=[Reading(decision="change", change="at 1 instead")]
    )
    reading = await Approvals(model).read("Shall I book it?", "Yes, but at 1 instead")
    assert reading == Reading(decision="change", change="at 1 instead")
    assert model.read_requests[0][-1].text == (
        "Jarvis asked: Shall I book it?\nThe user replied: Yes, but at 1 instead"
    )


def test_an_answer_goes_to_the_paused_change_it_was_asked_about():
    question = Question((PAUSED,), "Shall I?", datetime.now(), {"configurable": {}})
    assert question.resume_with(Reading(decision="approve")) == {
        "pause-1": {"decisions": [{"type": "approve"}]}
    }


def test_a_change_is_a_rejection_with_the_reason():
    change = decision_for(Reading(decision="change", change="make it 1 PM"))
    assert change["type"] == "reject"
    assert "make it 1 PM" in change["message"]
    assert "said no" in decision_for(Reading(decision="reject"))["message"]
