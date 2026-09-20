import pytest

from contextlens.observations import ObservationStore


def test_deferred_observation_is_recoverable_by_stable_handle(tmp_path):
    store = ObservationStore(tmp_path)
    observation = store.add(
        kind="test_output",
        summary="refresh test failed",
        content="expected 3600, got 900",
        source="pytest",
    )

    store.defer(observation.handle)

    assert store.active() == []
    assert store.deferred()[0].handle == observation.handle
    assert store.read(observation.handle).content == "expected 3600, got 900"
    restored = store.restore(observation.handle)
    assert restored.status == "keep"
    assert store.active()[0].handle == observation.handle


def test_pinned_observation_cannot_be_deferred(tmp_path):
    store = ObservationStore(tmp_path)
    constraint = store.add(
        kind="user_constraint",
        summary="do not change the public API",
        content="do not change the public API",
        pinned=True,
    )

    with pytest.raises(ValueError, match="pinned"):
        store.defer(constraint.handle)


def test_handles_detect_tampering_and_survive_reload(tmp_path):
    store = ObservationStore(tmp_path)
    item = store.add(kind="diff", summary="one line changed", content="+ return 42")
    reloaded = ObservationStore(tmp_path)
    assert reloaded.read(item.handle).summary == "one line changed"
    (tmp_path / "records" / f"{item.handle}.json").write_text("{}")
    with pytest.raises(RuntimeError, match="integrity"):
        reloaded.read(item.handle)


def test_descriptors_age_without_exposing_content(tmp_path):
    store = ObservationStore(tmp_path)
    item = store.add(
        kind="traceback", summary="failure in refresh", content="secret raw body"
    )
    store.advance()
    descriptor = store.descriptors()[0]
    assert descriptor == {
        "id": item.handle,
        "type": "traceback",
        "summary": "failure in refresh",
        "source": None,
        "age_steps": 1,
    }
    assert "secret raw body" not in str(descriptor)
