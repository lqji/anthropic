"""Graph shape, node stubs, routing and the history window. No database needed."""

import inspect
from collections.abc import Callable
from typing import get_type_hints

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from clinic.config import Settings
from clinic.workflows.graph import build_graph
from clinic.workflows.history import history_window
from clinic.workflows.nodes import NODES
from clinic.workflows.nodes._stub import NOT_IMPLEMENTED, NOT_IMPLEMENTED_REPLY
from clinic.workflows.nodes.safety import MissingReplyError
from clinic.workflows.routing import route_intent, route_start
from clinic.workflows.state import INTENTS, ConversationState, new_turn, turn_id_for

EXPECTED_NODES = {
    "classify",
    "decline",
    "knowledge",
    "find_slots",
    "book",
    "confirm_booking",
    "cancel",
    "request_approval",
    "await_approval",
    "apply_decision",
    "clarify",
    "other",
    "finalize",
}


def _conversation(count: int) -> list[BaseMessage]:
    """`count` messages alternating human, AI, human, ..."""
    return [
        HumanMessage(content=f"patient {n}") if n % 2 == 0 else AIMessage(content=f"reply {n}")
        for n in range(count)
    ]


# --- Graph and node stubs -------------------------------------------------------------------


def test_every_graph_node_is_a_stub_with_the_state_signature(
    make_settings: Callable[..., Settings],
) -> None:
    graph = build_graph(make_settings(), None)

    assert set(graph.get_graph().nodes) - {"__start__", "__end__"} == EXPECTED_NODES
    assert set(NODES) == EXPECTED_NODES
    for name, node in NODES.items():
        hints = get_type_hints(node)
        assert list(inspect.signature(node).parameters) == ["state"], name
        assert hints["state"] is ConversationState, name
        assert hints["return"] == dict[str, object], name
        assert isinstance(node({**new_turn("hello", "t1"), "reply": "ok"}), dict), name


def test_a_turn_runs_end_to_end_with_an_honest_fallback(
    make_settings: Callable[..., Settings],
) -> None:
    graph = build_graph(make_settings(), None)

    result = graph.invoke(new_turn("hello", "t1"))

    assert result["outcome"] == NOT_IMPLEMENTED
    assert [type(m) for m in result["messages"]] == [HumanMessage, AIMessage]
    assert result["messages"][-1].text == NOT_IMPLEMENTED_REPLY


@pytest.mark.parametrize("missing", [{}, {"reply": "   "}])
def test_a_turn_without_a_reply_fails_loudly(
    make_settings: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
    missing: dict[str, object],
) -> None:
    def forgetful_clarify(state: ConversationState) -> dict[str, object]:
        return missing

    monkeypatch.setitem(NODES, "clarify", forgetful_clarify)
    graph = build_graph(make_settings(), None)

    with pytest.raises(MissingReplyError):
        graph.invoke(new_turn("hello", "t1"))


def test_new_turn_clears_the_per_turn_fields() -> None:
    state = new_turn("hi", "t1")
    assert [m.text for m in state["messages"]] == ["hi"]
    assert state["intent"] == "unclear"
    assert (state["reply"], state["outcome"], state["sources"]) == ("", "", [])
    assert "offered_slots" not in state  # kept from earlier turns for "book the first one"


def test_turn_ids_are_stable_and_name_both_messages(make_settings: Callable[..., Settings]) -> None:
    turn_id = turn_id_for("conv-1", 3)
    assert turn_id == turn_id_for("conv-1", 3) == "conv-1:3"
    assert turn_id != turn_id_for("conv-1", 4)

    result = build_graph(make_settings(), None).invoke(new_turn("hi", turn_id))

    assert result["turn_id"] == "conv-1:3"
    assert [m.id for m in result["messages"]] == ["conv-1:3:patient", "conv-1:3:reply"]


# --- route_start ----------------------------------------------------------------------------


def test_route_start_classifies_by_default() -> None:
    assert route_start(new_turn("When is Dr. Salma free?", "t1")) == "classify"
    assert route_start({}) == "classify"


def test_route_start_declines_medical_advice_on_the_latest_patient_message() -> None:
    seen: list[str] = []

    def asks_for_advice(text: str) -> bool:
        seen.append(text)
        return "cream" in text

    state: ConversationState = {
        "messages": [
            HumanMessage(content="hello"),
            AIMessage(content="How can I help?"),
            HumanMessage(content="What cream should I use?"),
            AIMessage(content="(reply still being written)"),
        ]
    }

    assert route_start(state, asks_for_advice) == "decline"
    assert route_start(new_turn("book me in", "t1"), asks_for_advice) == "classify"
    assert seen == ["What cream should I use?", "book me in"]


# --- route_intent ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("intent", "node"),
    [
        ("clinic_question", "knowledge"),
        ("find_slots", "find_slots"),
        ("book", "book"),
        ("cancel", "cancel"),
        ("other", "other"),
        ("unclear", "clarify"),
        ("something_new", "clarify"),  # unknown intent: ask, never guess
    ],
)
def test_route_intent_sends_each_intent_to_its_node(intent: str, node: str) -> None:
    assert route_intent({"intent": intent, "confidence": 0.9}, min_confidence=0.6) == node


def test_every_known_intent_has_a_route() -> None:
    routes = {route_intent({"intent": i, "confidence": 1.0}, min_confidence=0.6) for i in INTENTS}
    assert routes == {"knowledge", "find_slots", "book", "cancel", "other", "clarify"}


@pytest.mark.parametrize(
    ("confidence", "node"), [(0.0, "clarify"), (0.59, "clarify"), (0.6, "book"), (1.0, "book")]
)
def test_route_intent_asks_when_confidence_is_below_the_threshold(
    confidence: float, node: str
) -> None:
    assert route_intent({"intent": "book", "confidence": confidence}, min_confidence=0.6) == node


def test_route_intent_without_a_classification_asks() -> None:
    assert route_intent({}, min_confidence=0.6) == "clarify"


def test_graph_uses_the_configured_confidence_threshold(
    make_settings: Callable[..., Settings], monkeypatch: pytest.MonkeyPatch
) -> None:
    def confident_other(state: ConversationState) -> dict[str, object]:
        return {"intent": "other", "confidence": 0.5}

    monkeypatch.setitem(NODES, "classify", confident_other)
    visited: list[str] = []
    for threshold in (0.6, 0.4):
        graph = build_graph(make_settings(intent_min_confidence=threshold), None)
        for update in graph.stream(new_turn("hi", "t1"), stream_mode="updates"):
            visited.extend(update)

    assert "clarify" in visited[: visited.index("finalize")]  # 0.5 < 0.6
    assert "other" in visited[visited.index("finalize") :]  # 0.5 >= 0.4


# --- history_window -------------------------------------------------------------------------


def test_window_keeps_the_last_messages_starting_with_a_human_one() -> None:
    messages = _conversation(30)

    window = history_window(messages, 10)

    assert window == messages[-10:]
    assert isinstance(window[0], HumanMessage)


def test_window_drops_a_leading_ai_message() -> None:
    messages = _conversation(30)

    window = history_window(messages, 11)  # the last 11 would start with an AI reply

    assert len(window) == 10
    assert isinstance(window[0], HumanMessage)
    assert window == messages[-10:]


def test_window_of_a_short_conversation_is_all_of_it() -> None:
    messages = _conversation(3)
    assert history_window(messages, 10) == messages
    assert history_window([], 10) == []
