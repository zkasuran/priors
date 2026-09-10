"""The vetting engine: turn priors held in memory into a decision.

The verdict is computed **deterministically** from what memory holds. No LLM
sits in the decision path, so the same memory always yields the same verdict
and a test can assert exactly how memory changes the call. The natural-language
rationale (priors/rationale.py) is a separate, optional layer that phrases a
verdict and never alters it.

Two independent ways memory changes the decision, both of which vanish when the
store is deleted:

  1. Individual history (the counterparty book + outcome ledger). A counterparty
     that stiffed us before flips a cold "transact" into "avoid".
  2. A learned guardrail (reflection over the ledger). A category-level prior,
     e.g. "cheap image sellers miss the brief 80% of the time", flips a cold
     "transact" into "caution" for a counterparty we have never met.

Delete memory and both are gone: every counterparty is a stranger and the
engine transacts blind. That is the eligibility gate.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any

from .memory import Memory, norm_addr

# Trust ordering, most conservative first. `tighten` never loosens a verdict.
_ORDER = {"avoid": 3, "caution": 2, "transact": 1}

DEFAULT_POLICY: dict[str, Any] = {
    "avoid_if_stiffed_gte": 2,          # two rug-pulls and we are out
    "avoid_if_delivery_rate_lt": 0.34,  # delivers less than a third of the time
    "caution_if_delivery_rate_lt": 0.75,
    "caution_if_avg_score_lt": 6.0,
    "min_history_for_trust": 2,         # below this, positive but low confidence
    "recency_half_life_days": 60.0,     # older outcomes weigh less
    "new_onchain_age_days": 3.0,        # a brand-new address is a caution prior
}


def tighten(a: str | None, b: str | None) -> str | None:
    """Return the more conservative of two actions (a guardrail can only tighten)."""
    if a is None:
        return b
    if b is None:
        return a
    return a if _ORDER[a] >= _ORDER[b] else b


@dataclass
class Reason:
    code: str
    text: str
    weight: float = 0.5


@dataclass
class Verdict:
    action: str                       # transact | caution | avoid
    confidence: float                 # 0..1
    score: float                      # -1..+1 trust score from history
    address: str
    svc_category: str
    terms: dict[str, Any] = field(default_factory=dict)
    reasons: list[Reason] = field(default_factory=list)
    priors_used: dict[str, Any] = field(default_factory=dict)
    guardrails_triggered: list[dict[str, Any]] = field(default_factory=list)
    alternative: dict[str, Any] | None = None
    cold_start: bool = False

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d

    def headline(self) -> str:
        return {
            "transact": "TRANSACT",
            "caution": "CAUTION",
            "avoid": "AVOID",
        }[self.action]


def _parse_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def aggregate(outcomes: list[dict[str, Any]], policy: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    """Roll a list of graded outcomes into the numbers the verdict needs.

    Scores are weighted by recency (exponential half-life) so a counterparty
    that has improved lately is judged on its recent record, not its worst day.
    """
    now = now or datetime.now(timezone.utc)
    half = max(policy.get("recency_half_life_days", 60.0), 1.0)

    n = len(outcomes)
    delivered = late = stiffed = partial = 0
    wsum = 0.0
    score_wsum = 0.0
    score_w = 0.0
    price_sum = 0.0
    price_n = 0
    last_seen = None
    for o in outcomes:
        outcome = o.get("outcome")
        if outcome == "delivered":
            delivered += 1
        elif outcome == "late":
            late += 1
        elif outcome == "stiffed":
            stiffed += 1
        elif outcome == "partial":
            partial += 1
        ts = _parse_ts(o.get("ts"))
        age_days = ((now - ts).total_seconds() / 86400.0) if ts else 0.0
        w = 0.5 ** (max(age_days, 0.0) / half)
        wsum += w
        if isinstance(o.get("score"), (int, float)):
            score_wsum += float(o["score"]) * w
            score_w += w
        if isinstance(o.get("price"), (int, float)):
            price_sum += float(o["price"])
            price_n += 1
        if ts and (last_seen is None or ts > last_seen):
            last_seen = ts

    good = delivered + late + 0.5 * partial
    total_gradeable = delivered + late + stiffed + partial
    delivery_rate = (good / total_gradeable) if total_gradeable else 0.0
    avg_score = (score_wsum / score_w) if score_w else None
    avg_price = (price_sum / price_n) if price_n else None
    late_rate = (late / total_gradeable) if total_gradeable else 0.0

    return {
        "n": n,
        "total": total_gradeable,
        "delivered": delivered,
        "late": late,
        "stiffed": stiffed,
        "partial": partial,
        "delivery_rate": round(delivery_rate, 3),
        "late_rate": round(late_rate, 3),
        "avg_score": round(avg_score, 2) if avg_score is not None else None,
        "avg_price": round(avg_price, 4) if avg_price is not None else None,
        "last_seen": last_seen.isoformat().replace("+00:00", "Z") if last_seen else None,
    }


def _score_history(agg: dict[str, Any]) -> float:
    """Map an aggregate to a trust score in [-1, +1]."""
    score = (agg["delivery_rate"] - 0.5) * 2 * 0.6
    if agg["avg_score"] is not None:
        score += ((agg["avg_score"] / 10.0) - 0.5) * 2 * 0.3
    score -= min(agg["stiffed"], 3) * 0.25
    score -= agg["late_rate"] * 0.15
    return round(max(-1.0, min(1.0, score)), 3)


def _eval_guardrail(g: dict[str, Any], agg: dict[str, Any], price: float | None) -> tuple[bool, str, str | None]:
    """Evaluate one learned guardrail against the situation.

    Returns (hit, note, action_hint). A guardrail encodes a cross-counterparty
    prior, so it fires even when we have no individual history.
    """
    kind = g.get("kind")
    ev = g.get("evidence", {})
    if kind == "cap_price_below_quality":
        # A category prior is for counterparties we do not know. A proven
        # counterparty's own record beats it, so only apply with thin history.
        if agg["total"] >= 2:
            return (False, "", None)
        floor = g.get("params", {}).get("min_price")
        if price is not None and floor is not None and price <= floor:
            fr = int(round(ev.get("fail_rate", 0) * 100))
            return (
                True,
                f"Category prior: {g.get('svc_category')} offers at or below "
                f"{floor} miss the brief {fr}% of the time (n={ev.get('n')}). "
                f"This offer is priced {price}.",
                "caution",
            )
    elif kind == "require_history":
        min_jobs = g.get("params", {}).get("min_jobs", 1)
        if agg["total"] < min_jobs:
            return (
                True,
                f"Category prior: {g.get('svc_category')} counterparties without a "
                f"graded track record have burned us before (n={ev.get('n')}). "
                f"No track record here yet.",
                "caution",
            )
    return (False, "", None)


def best_alternative(mem: Memory, svc_category: str, exclude: str) -> dict[str, Any] | None:
    """The best counterparty with real history in this category, excluding one.

    Makes an "avoid" verdict actionable: who to hire instead.
    """
    exclude = norm_addr(exclude)
    best = None
    best_key = -1.0
    for cp in mem.list_counterparties():
        addr = norm_addr(cp.get("address", ""))
        if addr == exclude:
            continue
        cats = cp.get("categories") or []
        if cats and svc_category not in cats:
            continue
        stats = cp.get("stats") or {}
        dr = stats.get("delivery_rate")
        avg = stats.get("avg_score")
        total = stats.get("total", 0)
        if not total or dr is None:
            continue
        key = dr * (avg / 10.0 if avg is not None else 0.5)
        if key > best_key:
            best_key = key
            best = {
                "address": cp.get("address"),
                "label": cp.get("label"),
                "delivery_rate": dr,
                "avg_score": avg,
                "jobs": total,
            }
    return best


def _derive_terms(action: str, agg: dict[str, Any], price: float | None) -> dict[str, Any]:
    if action == "avoid":
        return {"recommend": "do not transact"}
    if action == "caution":
        cap = None
        if agg["avg_price"] is not None and price is not None:
            cap = round(min(price, agg["avg_price"]), 4)
        elif agg["avg_price"] is not None:
            cap = agg["avg_price"]
        else:
            cap = price
        return {
            "escrow": True,
            "max_price": cap,
            "hold_back": 0.5,
            "checkpoints": ["request a sample first", "release payment on milestone"],
        }
    # transact
    cap = round(agg["avg_price"] * 1.2, 4) if agg["avg_price"] is not None else price
    return {"escrow": True, "max_price": cap, "checkpoints": []}


def vet(
    mem: Memory,
    address: str,
    svc_category: str,
    offer: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> Verdict:
    """Decide whether to transact with `address` for a `svc_category` job.

    Reads every prior memory holds and returns a deterministic verdict. With an
    empty memory this returns a low-confidence "transact" (blind), which is
    exactly what a stateless agent does.
    """
    offer = offer or {}
    price = offer.get("price")
    policy = {**DEFAULT_POLICY, **(mem.get_policy() or {})}

    outcomes = mem.outcomes_for(address)
    agg = aggregate(outcomes, policy, now=now)
    cp = mem.get_counterparty(address) or {}
    onchain = cp.get("onchain")
    label = cp.get("label")
    guardrails = mem.guardrails_for(svc_category)

    reasons: list[Reason] = []
    triggered: list[dict[str, Any]] = []
    guard_action: str | None = None

    # 1) learned guardrails (cross-counterparty priors) fire regardless of history
    for g in guardrails:
        hit, note, hint = _eval_guardrail(g, agg, price)
        if hit:
            triggered.append(g)
            reasons.append(Reason(f"guardrail:{g.get('slug')}", note, 0.6))
            guard_action = tighten(guard_action, hint)

    # 2) individual history
    cold = agg["total"] == 0 and onchain is None
    if cold:
        base_action = "transact"
        base_conf = 0.2
        score = 0.0
        if triggered:
            reasons.append(Reason(
                "no-priors",
                "No individual history with this counterparty; the learned "
                "category priors below are what change the call.",
                0.8,
            ))
        else:
            reasons.append(Reason(
                "no-priors",
                "No prior dealings with this counterparty and no on-chain history. "
                "Proceeding blind, the way a stateless agent would.",
                1.0,
            ))
    else:
        if agg["total"] > 0:
            score = _score_history(agg)
            if agg["stiffed"] >= policy["avoid_if_stiffed_gte"]:
                base_action = "avoid"
                reasons.append(Reason(
                    "stiffed",
                    f"Stiffed us {agg['stiffed']} time(s): took the job and did not "
                    f"deliver. Policy avoids at {policy['avoid_if_stiffed_gte']}.",
                    1.0,
                ))
            elif agg["delivery_rate"] < policy["avoid_if_delivery_rate_lt"]:
                base_action = "avoid"
                reasons.append(Reason(
                    "low-delivery",
                    f"Delivered only {int(agg['delivery_rate']*100)}% of "
                    f"{agg['total']} graded jobs.",
                    1.0,
                ))
            elif (
                agg["delivery_rate"] < policy["caution_if_delivery_rate_lt"]
                or (agg["avg_score"] is not None and agg["avg_score"] < policy["caution_if_avg_score_lt"])
            ):
                base_action = "caution"
                reasons.append(Reason(
                    "mixed-record",
                    f"Mixed record: {int(agg['delivery_rate']*100)}% delivery"
                    + (f", average score {agg['avg_score']}/10" if agg["avg_score"] is not None else "")
                    + f" over {agg['total']} jobs.",
                    0.8,
                ))
            else:
                base_action = "transact"
                reasons.append(Reason(
                    "good-record",
                    f"Reliable: {int(agg['delivery_rate']*100)}% delivery"
                    + (f", average score {agg['avg_score']}/10" if agg["avg_score"] is not None else "")
                    + f" over {agg['total']} graded jobs.",
                    1.0,
                ))
            base_conf = min(0.95, 0.35 + 0.15 * agg["total"])
            if 0 < agg["total"] < policy["min_history_for_trust"] and base_action == "transact":
                base_conf = min(base_conf, 0.5)
        else:
            # no graded jobs, but there is an on-chain footprint to reason from
            base_action = "transact"
            base_conf = 0.25
            score = 0.0
            reasons.append(Reason("no-history", "No graded jobs with this counterparty yet.", 0.6))

        # on-chain prior (whenever we have read Base for this address)
        if onchain:
            age = onchain.get("age_days")
            txc = onchain.get("tx_count")
            fresh = (age is not None and age < policy["new_onchain_age_days"]) or txc == 0
            if fresh:
                detail = f"{txc} txs" + (f", age {age}d" if age is not None else "")
                reasons.append(Reason(
                    "fresh-onchain",
                    f"On Base this address has little on-chain track record ({detail}).",
                    0.5,
                ))
                if base_action == "transact":
                    base_action = "caution"
            elif age is not None and age > 30:
                reasons.append(Reason(
                    "established-onchain",
                    f"Established on Base (age {age}d, {txc} txs).",
                    0.3,
                ))
                base_conf = min(0.97, base_conf + 0.05)

    action = tighten(base_action, guard_action)
    terms = _derive_terms(action, agg, price)

    alt = None
    if action == "avoid":
        alt = best_alternative(mem, svc_category, exclude=address)
        if alt:
            reasons.append(Reason(
                "alternative",
                f"Hire {alt.get('label') or alt.get('address')} instead: "
                f"{int((alt.get('delivery_rate') or 0)*100)}% delivery over "
                f"{alt.get('jobs')} jobs.",
                0.7,
            ))

    confidence = round(base_conf if not (triggered and cold) else max(base_conf, 0.55), 3)

    return Verdict(
        action=action,
        confidence=confidence,
        score=score,
        address=address,
        svc_category=svc_category,
        terms=terms,
        reasons=reasons,
        priors_used={"aggregate": agg, "label": label, "onchain": onchain},
        guardrails_triggered=triggered,
        alternative=alt,
        cold_start=cold and not triggered,
    )
