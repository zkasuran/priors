from priors.learn import record_outcome, synthesize_guardrails, refresh_counterparty


def test_record_outcome_compounds_the_card(mem):
    record_outcome(mem, "0xa", "image", "delivered", score=9, price=2.0, label="MemeMaster")
    record_outcome(mem, "0xa", "image", "stiffed", score=0, price=2.0)
    cp = mem.get_counterparty("0xa")
    assert cp["label"] == "MemeMaster"
    assert cp["stats"]["total"] == 2
    assert cp["stats"]["delivered"] == 1 and cp["stats"]["stiffed"] == 1
    assert cp["categories"] == ["image"]
    assert cp["last_outcome"] == "stiffed"


def test_card_tracks_multiple_categories(mem):
    record_outcome(mem, "0xa", "image", "delivered", score=9, price=2.0)
    record_outcome(mem, "0xa", "copy", "delivered", score=8, price=1.0)
    cp = mem.get_counterparty("0xa")
    assert cp["categories"] == ["copy", "image"]


def test_synthesize_cheap_miss_guardrail(mem):
    for i in range(6):
        record_outcome(mem, f"0xcheap{i}", "image", "stiffed", price=0.5)
    for i in range(4):
        record_outcome(mem, f"0xdear{i}", "image", "delivered", score=9, price=5.0)
    written = synthesize_guardrails(mem, "image")
    slugs = {g["slug"] for g in written}
    assert "cheap-miss" in slugs
    g = next(x for x in written if x["slug"] == "cheap-miss")
    assert g["kind"] == "cap_price_below_quality"
    assert g["evidence"]["fail_rate"] >= 0.6
    # persisted and idempotent
    assert mem.guardrails_for("image")
    again = synthesize_guardrails(mem, "image")
    assert any(x["slug"] == "cheap-miss" for x in again)


def test_no_guardrail_without_enough_evidence(mem):
    record_outcome(mem, "0xc1", "image", "stiffed", price=0.5)
    record_outcome(mem, "0xc2", "image", "stiffed", price=0.5)
    # only 2 samples, below MIN_COHORT
    assert synthesize_guardrails(mem, "image") == []
