"""R1 experiment: each conversation keeps its own workflow state, and only Postgres keeps it
across a restart.

The smallest graph that answers the R1 question, with the project's FakeLLM:

    START --> respond --> END

`respond` classifies the latest patient message and says how many earlier messages it can see,
so every reply shows how much of its own conversation the graph remembers.

`InMemorySaver` is allowed here and nowhere else: it forgets everything when the process ends.
The real service uses `PostgresSaver` (see docs/adr/checkpoint-design.md).

Run it (needs a PostgreSQL, for example the dev database from compose.dev.yaml):

    DATABASE_URL=postgresql://clinic:change-me-local-only@localhost:5432/clinic \\
        uv run python experiments/checkpoints/checkpoints_demo.py
"""

import dataclasses
import datetime as dt
import os
import sys
from typing import Annotated, Any, TypedDict
from uuid import uuid4

import psycopg
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from psycopg import Connection
from psycopg.rows import DictRow, dict_row

from clinic.llm.fake import FakeLLM
from clinic.llm.port import ClassifyContext
from clinic.workflows.history import history_window

TODAY = dt.date(2026, 11, 4)
HISTORY_WINDOW = 10  # the service's default (HISTORY_WINDOW in .env.example)
# The tables PostgresSaver.setup() creates. The first three hold data per thread.
TABLES = ("checkpoints", "checkpoint_blobs", "checkpoint_writes", "checkpoint_migrations")

# Strict: only plain data and LangChain's own types load back (LANGGRAPH_STRICT_MSGPACK=true).
# Permissive: any Python class loads back, with a warning. Passed explicitly here, so the
# experiment shows both whatever the environment says.
STRICT = JsonPlusSerializer(allowed_msgpack_modules=None)
PERMISSIVE = JsonPlusSerializer(allowed_msgpack_modules=True)


class State(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]


def respond(state: State) -> dict[str, list[AnyMessage]]:
    text = str(state["messages"][-1].content)
    intent = FakeLLM().classify(text, ClassifyContext(today=TODAY)).intent
    earlier = len(state["messages"]) - 1
    return {"messages": [AIMessage(f"[{intent}] I can see {earlier} earlier messages.")]}


Graph = CompiledStateGraph[State, None, State, State]


def build(saver: BaseCheckpointSaver[str]) -> Graph:
    builder = StateGraph(State)
    builder.add_node("respond", respond)
    builder.add_edge(START, "respond")
    builder.add_edge("respond", END)
    return builder.compile(checkpointer=saver)


def thread(thread_id: str) -> RunnableConfig:
    """One thread id is one conversation: its checkpoints are stored under that id."""
    return {"configurable": {"thread_id": thread_id}}


def say(graph: Graph, thread_id: str, text: str) -> str:
    out = graph.invoke({"messages": [HumanMessage(text)]}, thread(thread_id))
    return str(out["messages"][-1].content)


def remembered(graph: Graph, thread_id: str) -> int:
    """How many messages the saver holds for this thread (0 if it has never seen it)."""
    return len(graph.get_state(thread(thread_id)).values.get("messages", []))


def connect(database_url: str) -> Connection[DictRow]:
    """The connection settings PostgresSaver needs; the service's checkpoint pool uses the same."""
    return psycopg.connect(database_url, autocommit=True, row_factory=dict_row)


def rows_per_table(conn: Connection[DictRow], thread_id: str) -> str:
    counts = []
    for table in TABLES[:3]:
        row = conn.execute(
            psycopg.sql.SQL("SELECT count(*) AS n FROM {} WHERE thread_id = %s").format(
                psycopg.sql.Identifier(table)
            ),
            (thread_id,),
        ).fetchone()
        counts.append(f"{table}={0 if row is None else row['n']}")
    size = conn.execute(
        "SELECT coalesce(sum(octet_length(blob)), 0) AS bytes FROM checkpoint_blobs"
        " WHERE thread_id = %s",
        (thread_id,),
    ).fetchone()
    counts.append(f"blob bytes={0 if size is None else size['bytes']}")
    return ", ".join(counts)


@dataclasses.dataclass
class Slot:
    """Not plain data: the kind of value the service must never put in its state."""

    doctor: str
    starts_at: str


class SlotState(TypedDict):
    slot: Any


def keep_slot(state: SlotState) -> SlotState:
    return {"slot": Slot("Dr. Salma Nasser", "09:00")}


def slot_after_reconnect(database_url: str, thread_id: str, serde: JsonPlusSerializer) -> str:
    """Store a dataclass, reconnect with a new saver, and report what comes back."""
    builder = StateGraph(SlotState)
    builder.add_node("keep_slot", keep_slot)
    builder.add_edge(START, "keep_slot")
    builder.add_edge("keep_slot", END)
    with connect(database_url) as conn:
        builder.compile(checkpointer=PostgresSaver(conn, serde=serde)).invoke(
            {"slot": None}, thread(thread_id)
        )
    with connect(database_url) as conn:
        graph = builder.compile(checkpointer=PostgresSaver(conn, serde=serde))
        value = graph.get_state(thread(thread_id)).values["slot"]
    return type(value).__name__


def run(database_url: str) -> list[str]:
    """Run the whole experiment and return what it observed, one line per fact."""
    seen: list[str] = []
    # Fresh thread ids on every run, so a rerun on the same database starts clean. Lines show
    # only the readable part (anna, omar).
    run_id = uuid4().hex[:8]
    anna, omar = f"anna-{run_id}", f"omar-{run_id}"

    # 1. In memory: two threads, separate state; a "restart" (a new saver) forgets both.
    memory = build(InMemorySaver())
    say(memory, anna, "What are your opening hours?")
    seen.append(f"1. memory, anna turn 2: {say(memory, anna, 'Do you take insurance?')!r}")
    seen.append(f"   memory, omar turn 1: {say(memory, omar, 'I want to book an appointment')!r}")
    seen.append(f"   held: anna={remembered(memory, anna)}, omar={remembered(memory, omar)}")
    restarted = build(InMemorySaver())
    seen.append(f"   after restart: anna={remembered(restarted, anna)} (state gone)")

    # 2. Postgres, the same conversations. setup() creates the tables (idempotent).
    with connect(database_url) as conn:
        PostgresSaver(conn).setup()
        graph = build(PostgresSaver(conn, serde=STRICT))
        say(graph, anna, "What are your opening hours?")
        seen.append(f"2. postgres, anna turn 1: rows {rows_per_table(conn, anna)}")
        say(graph, anna, "Do you take insurance?")
        seen.append(f"   postgres, anna turn 2: rows {rows_per_table(conn, anna)}")
        say(graph, omar, "I want to book an appointment")
        seen.append(f"   held: anna={remembered(graph, anna)}, omar={remembered(graph, omar)}")
        tables = conn.execute(
            "SELECT tablename FROM pg_tables WHERE tablename = ANY(%s) ORDER BY tablename",
            (list(TABLES),),
        ).fetchall()
        seen.append(f"   tables: {', '.join(t['tablename'] for t in tables)}")

    # 3. Restart: the connection, the saver and the graph are all new. Only the database is not.
    with connect(database_url) as conn:
        graph = build(PostgresSaver(conn, serde=STRICT))
        held = f"anna={remembered(graph, anna)}, omar={remembered(graph, omar)}"
        seen.append(f"3. after restart: {held}")
        seen.append(f"   anna turn 3: {say(graph, anna, 'Can I cancel my appointment?')!r}")

        # 4. The checkpoint keeps every message; the prompt gets a bounded window.
        for n in range(4, 13):
            say(graph, anna, f"Question number {n}")
        messages = graph.get_state(thread(anna)).values["messages"]
        window = history_window(messages, HISTORY_WINDOW)
        seen.append(
            f"4. after 12 turns: checkpoint holds {len(messages)} messages, "
            f"the prompt window {len(window)} (first is {window[0].type})"
        )
        seen.append(f"   rows {rows_per_table(conn, anna)}")

    # 5. Strict msgpack: a dataclass in the state does not come back as a dataclass.
    strict = slot_after_reconnect(database_url, f"slot-s-{run_id}", STRICT)
    permissive = slot_after_reconnect(database_url, f"slot-p-{run_id}", PERMISSIVE)
    seen.append(f"5. dataclass after restart, strict: {strict}")
    seen.append(f"   dataclass after restart, permissive: {permissive}")
    return [line.replace(f"-{run_id}", "") for line in seen]


if __name__ == "__main__":
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("Set DATABASE_URL to a PostgreSQL database (see the docstring).")
    for line in run(url):
        print(line)
