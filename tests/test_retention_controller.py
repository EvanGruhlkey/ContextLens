from contextlens.jev_gateway import Evaluation, GatewayError
from contextlens.observations import ObservationStore
from contextlens.retention import RetentionController


class Judge:
    def __init__(self, probabilities=None, error=None):
        self.probabilities = probabilities or {}
        self.error = error
        self.state = None

    def evaluate(self, state, questions):
        self.state = state
        if self.error:
            raise self.error
        return Evaluation(self.probabilities, "typesafe-ai/jev", 10, 2, "0", 3.0)


def test_retention_defers_low_usefulness_and_keeps_pins(tmp_path):
    store = ObservationStore(tmp_path)
    old = store.add(kind="traceback", summary="superseded failure", content="raw")
    current = store.add(kind="diff", summary="current patch", content="diff")
    pin = store.add(
        kind="user_constraint", summary="preserve API", content="preserve API"
    )
    judge = Judge({old.handle: 0.1, current.handle: 0.9})

    decision = RetentionController(judge=judge).evaluate(
        task="fix expiry", focus="verify patch", store=store
    )

    assert decision.deferred == (old.handle,)
    assert decision.kept == (current.handle, pin.handle)
    assert {item.handle for item in store.active()} == {current.handle, pin.handle}
    assert "raw" not in str(judge.state)
    assert pin.handle not in judge.probabilities


def test_retention_failure_keeps_the_working_set(tmp_path):
    store = ObservationStore(tmp_path)
    item = store.add(kind="tool_result", summary="useful result", content="raw")
    decision = RetentionController(judge=Judge(error=GatewayError("down"))).evaluate(
        task="task", focus="focus", store=store
    )
    assert decision.fallback_reason == "gateway_unavailable"
    assert decision.kept == (item.handle,)
    assert store.active()[0].handle == item.handle
