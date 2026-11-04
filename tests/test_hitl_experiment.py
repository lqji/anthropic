"""The R2 experiment (experiments/hitl) still runs and still shows what the research note says."""

import importlib.util
from pathlib import Path
from types import ModuleType

DEMO = Path(__file__).resolve().parent.parent / "experiments" / "hitl" / "hitl_demo.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("hitl_demo", DEMO)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_experiment_pauses_resumes_and_reruns_the_waiting_node() -> None:
    demo = _load()

    seen = "\n".join(demo.run())

    assert "paused; the caller got" in seen
    assert "next node: ('ask_staff',)" in seen
    assert "reply so far: None" in seen  # the patient has no answer while paused
    assert "another thread answered at once" in seen
    assert "resumed with 'approved': 'Your appointment has been cancelled.'" in seen
    assert "ask_staff started 2 times" in seen  # the waiting node re-runs on resume
    assert "next node after finishing: ()" in seen
    assert "Not approved: doctor needs this visit" in seen


def test_the_experiment_is_repeatable() -> None:
    demo = _load()

    assert demo.run() == demo.run()
