"""The eligibility gate, encoded.

The hackathon's pass/fail gate: "delete the memory layer; if it still works it
is a wrapper." These tests delete the memory layer and assert that Priors stops
working, i.e. it falls back to transacting blind. Two independent memory
mechanisms are checked, and both collapse when the store is wiped.
"""

from priors.memory import Memory
from priors.engine import vet
from priors.learn import record_outcome, synthesize_guardrails


def test_individual_history_flips_transact_to_avoid_and_back(mem):
    addr = "0xBAD"

    # Cold: no priors. Priors proceeds blind, exactly like a stateless agent.
    cold = vet(mem, addr, "image")
    assert cold.action == "transact"
    assert cold.cold_start is True

    # Two sessions of getting stiffed compound in the ledger.
    record_outcome(mem, addr, "image", "stiffed", price=1.0, label="PixelBot")
    record_outcome(mem, addr, "image", "stiffed", price=1.0)

    warm = vet(mem, addr, "image")
    assert warm.action == "avoid"          # memory changed the decision
    assert warm.cold_start is False
    assert any(r.code == "stiffed" for r in warm.reasons)

    # Delete the memory layer. Re-open the same path: it is empty.
    mem.wipe()
    reborn = Memory(mem.db_path)
    blind = vet(reborn, addr, "image")
    assert blind.action == "transact"      # collapsed back to blind
    assert blind.cold_start is True


def test_learned_guardrail_changes_a_never_met_counterparty(mem):
    # Grade a cohort: cheap sellers miss, the pricier tier delivers.
    for i in range(6):
        record_outcome(mem, f"0xcheap{i}", "image", "stiffed", price=0.5)
    for i in range(4):
        record_outcome(mem, f"0xdear{i}", "image", "delivered", score=9, price=5.0)

    # Reflection turns that ledger into a category-level prior.
    learned = synthesize_guardrails(mem, "image")
    assert any(g["slug"] == "cheap-miss" for g in learned)

    # A counterparty we have NEVER dealt with, offering at the cheap tier.
    verdict = vet(mem, "0xNEVERMET", "image", offer={"price": 0.5})
    assert verdict.action == "caution"     # the learned prior fired
    assert verdict.guardrails_triggered

    # Delete memory: the learned prior is gone, so it transacts blind.
    mem.wipe()
    reborn = Memory(mem.db_path)
    blind = vet(reborn, "0xNEVERMET", "image", offer={"price": 0.5})
    assert blind.action == "transact"
    assert blind.cold_start is True


def test_avoid_verdict_names_a_better_alternative(mem):
    record_outcome(mem, "0xBAD", "image", "stiffed", price=1.0, label="PixelBot")
    record_outcome(mem, "0xBAD", "image", "stiffed", price=1.0)
    for _ in range(4):
        record_outcome(mem, "0xGOOD", "image", "delivered", score=9, price=2.0, label="MemeMaster")

    v = vet(mem, "0xBAD", "image")
    assert v.action == "avoid"
    assert v.alternative is not None
    assert v.alternative["label"] == "MemeMaster"
