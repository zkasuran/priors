"""Writing outcomes back, and the reflection loop that learns guardrails.

`record_outcome` appends to the journal (the append-only ledger) and folds the
counterparty's whole history into its roster card, so the record compounds
across sessions. `synthesize_guardrails` is our own reflection loop over the
ledger: it detects cross-counterparty patterns ("cheap image sellers miss the
brief") and writes them back as guardrails that then change future verdicts.

We build reflection ourselves on Sibyl's journal + entities tiers rather than
calling the paid-tier Learner, so the decision-changing brain is ours and runs
free and offline.
"""

from __future__ import annotations

from typing import Any

from .engine import DEFAULT_POLICY, aggregate
from .memory import Memory, norm_addr, now_iso

# reflection thresholds
MIN_COHORT = 4          # need at least this many samples to trust a pattern
FAIL_THRESH = 0.6       # a cohort "miss rate" this high is worth a guardrail
FIRST_JOB_MIN = 3       # samples needed for the "no track record" pattern

_FAIL_OUTCOMES = {"stiffed", "partial"}


def _is_fail(o: dict[str, Any]) -> bool:
    if o.get("outcome") in _FAIL_OUTCOMES:
        return True
    s = o.get("score")
    return isinstance(s, (int, float)) and s < 5


def refresh_counterparty(mem: Memory, address: str, label: str | None = None, svc_category: str | None = None) -> dict[str, Any]:
    """Recompute a counterparty's roster card from the full journal."""
    outs = mem.outcomes_for(address)
    agg = aggregate(outs, DEFAULT_POLICY)
    cp = mem.get_counterparty(address) or {"address": norm_addr(address)}
    if label:
        cp["label"] = label
    cp.setdefault("label", None)
    cats = set(cp.get("categories") or [])
    for o in outs:
        if o.get("svc_category"):
            cats.add(o["svc_category"])
    if svc_category:
        cats.add(svc_category)
    cp["categories"] = sorted(cats)
    cp["stats"] = agg
    cp["last_outcome"] = outs[0].get("outcome") if outs else None
    cp["updated"] = now_iso()
    mem.put_counterparty(address, cp)
    return cp


def record_outcome(
    mem: Memory,
    address: str,
    svc_category: str,
    outcome: str,
    score: float | None = None,
    price: float | None = None,
    task: str | None = None,
    label: str | None = None,
    notes: str | None = None,
    ts: str | None = None,
) -> dict[str, Any]:
    """Grade a completed job: append to the ledger, then fold it into the card."""
    if outcome not in ("delivered", "late", "partial", "stiffed"):
        raise ValueError(f"unknown outcome: {outcome!r}")
    payload = {
        "address": norm_addr(address),
        "svc_category": svc_category,
        "outcome": outcome,
        "score": score,
        "price": price,
        "task": task,
        "notes": notes,
        "ts": ts or now_iso(),
    }
    mem.record_outcome_event(payload, ts=payload["ts"])
    return refresh_counterparty(mem, address, label=label, svc_category=svc_category)


def synthesize_guardrails(mem: Memory, svc_category: str, now: Any = None) -> list[dict[str, Any]]:
    """Reflect over the ledger for one category and write learned guardrails.

    Returns the guardrails written this pass (idempotent: re-running refreshes
    the evidence in place).
    """
    outs = [o for o in mem.outcomes_for(svc_category=svc_category) if o.get("price") is not None]
    written: list[dict[str, Any]] = []

    # Pattern A: a cheap tier misses the brief.
    # Find the price threshold that best SEPARATES the failing cheap tier from
    # the rest (maximize cheap_fail - dear_fail), rather than a fixed split.
    priced = [o for o in outs if isinstance(o.get("price"), (int, float))]
    if len(priced) >= MIN_COHORT:
        uniq = sorted({o["price"] for o in priced})
        best = None  # (separation, t, n_cheap, cheap_fail, dear_fail)
        for t in uniq:
            cheap = [o for o in priced if o["price"] <= t]
            dear = [o for o in priced if o["price"] > t]
            if len(cheap) < MIN_COHORT:
                continue
            cf = sum(_is_fail(o) for o in cheap) / len(cheap)
            df = (sum(_is_fail(o) for o in dear) / len(dear)) if dear else 0.0
            if cf < FAIL_THRESH or cf <= df + 0.2:
                continue
            sep = cf - df
            cand = (sep, len(cheap), t, cf, df)
            if best is None or cand[:2] > best[:2]:  # max separation, then larger cohort
                best = cand
        if best is not None:
            _sep, n_cheap, t, cf, df = best
            body = {
                "slug": "cheap-miss",
                "svc_category": svc_category,
                "kind": "cap_price_below_quality",
                "text": (
                    f"{svc_category} offers at or below {round(t, 4)} miss the "
                    f"brief {int(round(cf*100))}% of the time "
                    f"(vs {int(round(df*100))}% above). Prefer the higher tier "
                    f"or require a sample first."
                ),
                "params": {"min_price": round(t, 4)},
                "evidence": {"n": n_cheap, "fail_rate": round(cf, 3),
                             "compare_fail_rate": round(df, 3)},
                "learned_ts": now_iso(),
            }
            mem.put_guardrail(svc_category, "cheap-miss", body)
            written.append(body)

    # Pattern B: counterparties with no track record burn us on the first job.
    by_addr: dict[str, list[dict[str, Any]]] = {}
    for o in outs:
        by_addr.setdefault(norm_addr(o.get("address", "")), []).append(o)
    firsts = []
    for addr, lst in by_addr.items():
        lst_sorted = sorted(lst, key=lambda x: x.get("ts") or "")
        if lst_sorted:
            firsts.append(lst_sorted[0])
    if len(firsts) >= FIRST_JOB_MIN:
        first_fail = sum(_is_fail(o) for o in firsts) / len(firsts)
        if first_fail >= 0.5:
            body = {
                "slug": "no-track-record",
                "svc_category": svc_category,
                "kind": "require_history",
                "text": (
                    f"{svc_category} counterparties with no graded history failed the "
                    f"first job {int(round(first_fail*100))}% of the time "
                    f"(n={len(firsts)}). Treat a fresh counterparty here with caution."
                ),
                "params": {"min_jobs": 1},
                "evidence": {"n": len(firsts), "fail_rate": round(first_fail, 3)},
                "learned_ts": now_iso(),
            }
            mem.put_guardrail(svc_category, "no-track-record", body)
            written.append(body)

    return written
