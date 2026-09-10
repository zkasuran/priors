from datetime import datetime, timezone, timedelta

from priors.engine import aggregate, vet, best_alternative, DEFAULT_POLICY, tighten
from priors.learn import record_outcome


NOW = datetime(2026, 9, 10, tzinfo=timezone.utc)


def _out(outcome, score=None, price=None, days_ago=0):
    ts = (NOW - timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")
    return {"outcome": outcome, "score": score, "price": price, "ts": ts}


def test_tighten_never_loosens():
    assert tighten("transact", "caution") == "caution"
    assert tighten("avoid", "caution") == "avoid"
    assert tighten(None, "transact") == "transact"


def test_aggregate_counts_and_delivery_rate():
    outs = [_out("delivered", 9, 2.0), _out("stiffed", 0, 2.0), _out("late", 7, 2.0), _out("partial", 5, 2.0)]
    agg = aggregate(outs, DEFAULT_POLICY, now=NOW)
    assert agg["total"] == 4
    assert agg["delivered"] == 1 and agg["stiffed"] == 1 and agg["late"] == 1 and agg["partial"] == 1
    # good = delivered + late + 0.5*partial = 2.5 / 4
    assert agg["delivery_rate"] == 0.625
    assert agg["avg_price"] == 2.0


def test_recency_weight_favors_recent_scores():
    # bad long ago, great recently -> recency-weighted score should lean positive
    outs = [_out("stiffed", 0, 2.0, days_ago=200), _out("delivered", 10, 2.0, days_ago=1)]
    agg = aggregate(outs, DEFAULT_POLICY, now=NOW)
    assert agg["avg_score"] > 8.0


def test_good_history_transacts_with_price_cap(mem):
    for _ in range(4):
        record_outcome(mem, "0xg", "image", "delivered", score=9, price=2.0)
    v = vet(mem, "0xg", "image")
    assert v.action == "transact"
    assert v.terms["max_price"] == 2.4         # avg 2.0 * 1.2
    assert v.confidence >= 0.7


def test_mixed_record_is_caution(mem):
    record_outcome(mem, "0xm", "image", "delivered", score=6, price=2.0)
    record_outcome(mem, "0xm", "image", "partial", score=4, price=2.0)
    record_outcome(mem, "0xm", "image", "delivered", score=5, price=2.0)
    v = vet(mem, "0xm", "image")
    assert v.action == "caution"
    assert "request a sample first" in v.terms["checkpoints"]


def test_fresh_onchain_address_downgrades_to_caution(mem):
    # one clean job would normally transact...
    record_outcome(mem, "0xnew", "image", "delivered", score=8, price=2.0)
    # ...but a brand-new on-chain footprint is a caution prior
    cp = mem.get_counterparty("0xnew")
    cp["onchain"] = {"age_days": 0.5, "tx_count": 1}
    mem.put_counterparty("0xnew", cp)
    v = vet(mem, "0xnew", "image")
    assert v.action == "caution"
    assert any(r.code == "fresh-onchain" for r in v.reasons)


def test_best_alternative_prefers_stronger_record(mem):
    for _ in range(3):
        record_outcome(mem, "0xok", "image", "delivered", score=6, price=2.0, label="OK")
    for _ in range(3):
        record_outcome(mem, "0xstar", "image", "delivered", score=10, price=2.0, label="Star")
    alt = best_alternative(mem, "image", exclude="0xzzz")
    assert alt["label"] == "Star"
