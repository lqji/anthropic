"""R2 experiment: a workflow that pauses for staff and resumes with their decision.

The smallest graph that shows LangGraph's human-in-the-loop pieces, with the project's FakeLLM
and no database:

    classify --cancel--> ask_staff (interrupt) --> apply --> END
             \\--other--> answer --> END

`InMemorySaver` is allowed here and nowhere else: it forgets everything when the process ends.
The real service uses `PostgresSaver` (see docs/adr/approval-design.md).

Not copied from the real flow: the service runs `is_emergency()` before it calls the model, and
this demo does not. Do not use this graph as a template for a patient-facing entry point.

Run it:  uv run python experiments/hitl/hitl_demo.py
"""

import datetime as dt
from collections.abc import Callable
from typing import TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, interrupt

from clinic.llm.fake import FakeLLM
from clinic.llm.port import ClassifyContext, LLMPort

# Every time `ask_staff` starts, one line is added. It proves what happens on resume.
EVENTS: list[str] = []


class State(TypedDict, total=False):
    text: str
    intent: str
    decision: str
    reason: str
    reply: str


def make_classify(llm: LLMPort) -> Callable[[State], State]:
    """The classify node. Like the real graph, it reaches the model only through `LLMPort`."""

    def classify(state: State) -> State:
        result = llm.classify(state["text"], ClassifyContext(today=dt.date(2026, 11, 4)))
        return {"intent": result.intent}

    return classify


def route(state: State) -> str:
    return "ask_staff" if state["intent"] == "cancel" else "answer"


def ask_staff(state: State) -> State:
    EVENTS.append("ask_staff started")
    # The first run stops here and hands the payload to the caller. After a resume, the node runs
    # again from its first line, and this call returns the value given to Command(resume=...).
    answer = interrupt({"question": "Cancel this appointment?", "text": state["text"]})
    return {"decision": answer["decision"], "reason": answer["reason"]}


def apply(state: State) -> State:
    if state["decision"] == "approved":
        return {"reply": "Your appointment has been cancelled."}
    return {"reply": f"Not approved: {state['reason']}. Your appointment is still booked."}


def answer(state: State) -> State:
    return {"reply": "Our front desk is open during clinic hours."}


Graph = CompiledStateGraph[State, None, State, State]


def build(saver: BaseCheckpointSaver[str], llm: LLMPort) -> Graph:
    builder = StateGraph(State)
    builder.add_node("classify", make_classify(llm))
    builder.add_node("ask_staff", ask_staff)
    builder.add_node("apply", apply)
    builder.add_node("answer", answer)
    builder.add_edge(START, "classify")
    builder.add_conditional_edges("classify", route, ["ask_staff", "answer"])
    builder.add_edge("ask_staff", "apply")
    builder.add_edge("apply", END)
    builder.add_edge("answer", END)
    return builder.compile(checkpointer=saver)


def thread(name: str) -> RunnableConfig:
    """One thread id is one conversation: its checkpoints are stored under that id."""
    return {"configurable": {"thread_id": name}}


def run() -> list[str]:
    """Run the whole experiment and return what it observed, one line per fact."""
    EVENTS.clear()
    saver = InMemorySaver()
    llm = FakeLLM()
    graph = build(saver, llm)
    seen: list[str] = []

    # 1. A cancel message pauses the graph. The call returns instead of waiting.
    first = graph.invoke({"text": "Cancel my appointment tomorrow"}, thread("anna"))
    pause = first["__interrupt__"][0]
    seen.append(f"1. paused; the caller got {pause.value}")
    seen.append(f"   next node: {graph.get_state(thread('anna')).next}")
    seen.append(f"   reply so far: {first.get('reply')!r} (nothing yet: the patient has no answer)")

    # 2. Another conversation is not affected by the pause.
    other = graph.invoke({"text": "What are your opening hours?"}, thread("omar"))
    seen.append(f"2. another thread answered at once: {other['reply']!r}")

    # 3. A new graph object on the same saver continues the paused thread. This is the shape of
    #    a restart; only a database-backed saver survives a real one.
    revived = build(saver, llm)
    seen.append(f"3. a new graph sees the pause: next = {revived.get_state(thread('anna')).next}")

    # 4. Resume with the staff decision.
    done = revived.invoke(Command(resume={"decision": "approved", "reason": "ok"}), thread("anna"))
    seen.append(f"4. resumed with 'approved': {done['reply']!r}")
    runs = EVENTS.count("ask_staff started")
    seen.append(f"   ask_staff started {runs} times (re-run on resume)")
    seen.append(f"   next node after finishing: {revived.get_state(thread('anna')).next}")

    # 5. A rejection goes down the other branch.
    graph.invoke({"text": "Cancel my appointment please"}, thread("sara"))
    rejected = graph.invoke(
        Command(resume={"decision": "rejected", "reason": "doctor needs this visit"}),
        thread("sara"),
    )
    seen.append(f"5. resumed with 'rejected': {rejected['reply']!r}")
    return seen


if __name__ == "__main__":
    for line in run():
        print(line)
