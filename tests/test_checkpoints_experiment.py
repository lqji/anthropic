"""The R1 experiment (experiments/checkpoints) still runs and still shows what the note says."""

import importlib.util
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

from tests.conftest import temporary_database

DEMO = (
    Path(__file__).resolve().parent.parent / "experiments" / "checkpoints" / "checkpoints_demo.py"
)


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("checkpoints_demo", DEMO)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Registered by name, as a normal import would be: the permissive serializer finds the
    # experiment's Slot class again by importing its module.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def url(database_url: str) -> Iterator[str]:
    with temporary_database(database_url) as url:
        yield url


def _rows(line: str) -> dict[str, int]:
    """'... rows checkpoints=3, checkpoint_blobs=3, ...' -> {'checkpoints': 3, ...}"""
    pairs = line.split("rows ", 1)[1].split(", ")
    return {name: int(value) for name, value in (pair.split("=") for pair in pairs)}


def test_threads_are_separate_and_only_postgres_survives_a_restart(url: str) -> None:
    seen = _load().run(url)
    text = "\n".join(seen)

    # Two thread ids, two separate conversations, in memory and in Postgres.
    assert "memory, anna turn 2: '[clinic_question] I can see 2 earlier messages.'" in text
    assert "memory, omar turn 1: '[book] I can see 0 earlier messages.'" in text
    assert text.count("held: anna=4, omar=2") == 2
    # A new InMemorySaver has nothing; a new PostgresSaver on the same database has everything.
    assert "after restart: anna=0 (state gone)" in text
    assert "3. after restart: anna=4, omar=2" in text
    assert "anna turn 3: '[cancel] I can see 4 earlier messages.'" in text
    assert "tables: checkpoint_blobs, checkpoint_migrations, checkpoint_writes, checkpoints" in text


def test_the_checkpoint_keeps_everything_and_the_prompt_gets_a_window(url: str) -> None:
    seen = _load().run(url)
    text = "\n".join(seen)

    assert "checkpoint holds 24 messages, the prompt window 10 (first is human)" in text
    turn_1, turn_2, turn_12 = (_rows(line) for line in seen if " rows " in line)
    # Every turn adds rows; nothing removes old checkpoints, and the stored bytes grow faster
    # than the turns (each blob holds the whole message list).
    assert turn_1["checkpoints"] < turn_2["checkpoints"] < turn_12["checkpoints"]
    assert turn_12["checkpoints"] == 12 * turn_1["checkpoints"]
    assert turn_12["blob bytes"] > 12 * turn_1["blob bytes"]


def test_strict_msgpack_turns_a_dataclass_into_a_dict_after_a_restart(url: str) -> None:
    seen = _load().run(url)

    assert "5. dataclass after restart, strict: dict" in seen
    assert "   dataclass after restart, permissive: Slot" in seen


def test_the_experiment_can_run_again_on_the_same_database(url: str) -> None:
    demo = _load()

    assert demo.run(url) == demo.run(url)
