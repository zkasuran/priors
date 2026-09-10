from priors.memory import Memory


def test_entity_roundtrip_case_insensitive(mem):
    mem.put_counterparty("0xAbC", {"address": "0xabc", "label": "X"})
    assert mem.get_counterparty("0xABC")["label"] == "X"
    assert mem.get_counterparty("0xabc")["label"] == "X"


def test_journal_is_append_only_and_ordered(mem):
    mem.record_outcome_event({"address": "0xa", "svc_category": "image", "outcome": "delivered", "score": 9})
    mem.record_outcome_event({"address": "0xa", "svc_category": "image", "outcome": "stiffed", "score": 0})
    outs = mem.outcomes_for("0xa")
    assert len(outs) == 2
    assert outs[0]["outcome"] == "stiffed"   # newest first
    assert mem.outcomes_for("0xb") == []
    assert len(mem.outcomes_for(svc_category="image")) == 2


def test_policy_reference_roundtrip(mem):
    assert mem.get_policy() is None
    mem.set_policy({"avoid_if_stiffed_gte": 3})
    assert mem.get_policy()["avoid_if_stiffed_gte"] == 3


def test_state_and_snapshot(mem):
    mem.set_focus({"task": "vet 0xa"})
    assert mem.get_focus()["task"] == "vet 0xa"
    mem.set_snapshot({"n": 1})
    assert mem.get_snapshot()["n"] == 1


def test_wipe_removes_everything(mem):
    mem.put_counterparty("0xa", {"address": "0xa"})
    assert mem.exists()
    mem.wipe()
    assert not mem.exists()
    reborn = Memory(mem.db_path)
    assert reborn.get_counterparty("0xa") is None
