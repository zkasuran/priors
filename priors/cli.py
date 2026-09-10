"""Priors command line.

    priors seed --reset          load the disclosed demo history
    priors vet <addr>            decide whether to transact (reads priors)
    priors vet <addr> --onchain  also read the counterparty's Base Sepolia state
    priors settle <addr> ...     grade a finished job (writes to the ledger)
    priors learn                 run reflection, synthesize guardrails
    priors roster                the remembered counterparties and their records
    priors guardrails            the learned category priors
    priors why <addr>            the priors behind the current verdict
    priors onchain <addr>        read + cache the Base Sepolia prior
    priors attest <addr> ...     attest a graded outcome on Base (EAS)
    priors snapshot              regenerate the dashboard data
    priors demo <addr>           cold vs warm, the fresh-session recall beat
    priors forget                delete the memory layer (the gate beat)
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from .memory import Memory
from .engine import vet, Verdict
from .learn import record_outcome, synthesize_guardrails
from . import seed as seedmod


def _short(addr: str) -> str:
    a = addr or ""
    return f"{a[:6]}…{a[-4:]}" if len(a) > 12 else a


def _c(s: str, code: str) -> str:
    return f"\033[{code}m{s}\033[0m" if sys.stdout.isatty() else s


_ACTION_COLOR = {"transact": "1;32", "caution": "1;33", "avoid": "1;31"}


def render_verdict(v: Verdict, explain: bool = False) -> str:
    label = v.priors_used.get("label") or _short(v.address)
    agg = v.priors_used.get("aggregate") or {}
    out = []
    out.append(f"Priors · vetting {label} ({_short(v.address)}) for a {v.svc_category} job\n")
    badge = _c(f" {v.headline()} ", _ACTION_COLOR[v.action] + ";7")
    out.append(f"  verdict   {badge}   confidence {v.confidence}   trust {v.score:+.2f}")
    if agg.get("total"):
        line = f"{agg['total']} jobs · {int(agg['delivery_rate']*100)}% delivered"
        if agg.get("avg_score") is not None:
            line += f" · avg {agg['avg_score']}/10"
        if agg.get("avg_price") is not None:
            line += f" · avg {agg['avg_price']}"
        out.append(f"  priors    {line}")
    elif v.cold_start:
        out.append(f"  priors    {_c('none, never dealt with this counterparty', '2')}")
    oc = v.priors_used.get("onchain")
    if oc:
        out.append(f"  on-chain  Base Sepolia · {oc.get('tx_count')} txs · "
                   f"{'contract' if oc.get('is_contract') else 'EOA'}")
    out.append("  because")
    for r in v.reasons:
        if r.code.startswith("guardrail:"):
            continue  # shown below as learned priors
        out.append(f"    • {r.text}")
    if v.guardrails_triggered:
        for g in v.guardrails_triggered:
            out.append(f"    ⚑ learned prior: {g.get('text')}")
    if v.action == "avoid" and v.alternative:
        alt = v.alternative
        out.append(f"  instead   {alt.get('label') or _short(alt.get('address'))} "
                   f"({int((alt.get('delivery_rate') or 0)*100)}% over {alt.get('jobs')} jobs)")
    terms = v.terms
    if v.action != "avoid":
        bits = []
        if terms.get("escrow"):
            bits.append("escrow")
        if terms.get("max_price") is not None:
            bits.append(f"cap {terms['max_price']}")
        if terms.get("checkpoints"):
            bits.append("; ".join(terms["checkpoints"]))
        out.append(f"  terms     {' · '.join(bits)}")
    if explain:
        from .rationale import explain as _explain
        out.append("\n  " + _explain(v).replace("\n", "\n  "))
    return "\n".join(out)


def _mem(args) -> Memory:
    return Memory(getattr(args, "db", None))


def cmd_db(args):
    m = _mem(args)
    print(m.db_path)


def cmd_seed(args):
    m = _mem(args)
    summary = seedmod.seed(m, reset=args.reset)
    print(f"seeded {summary['outcomes']} outcomes across {summary['counterparties']} "
          f"counterparties  [synthetic demo data]")
    if summary["guardrails"]:
        print("learned guardrails:", ", ".join(summary["guardrails"]))
    print("memory:", m.db_path)


def cmd_vet(args):
    m = _mem(args)
    if args.onchain:
        try:
            from .onchain import cache_onchain_prior
            cache_onchain_prior(m, args.address)
        except Exception as e:
            print(_c(f"(on-chain read skipped: {e})", "2"))
    offer = {"price": args.price} if args.price is not None else None
    v = vet(m, args.address, args.category, offer=offer)
    if args.json:
        print(json.dumps(v.to_dict(), indent=2, default=str))
    else:
        print(render_verdict(v, explain=args.explain))


def cmd_why(args):
    args.onchain = False
    args.price = None
    args.explain = True
    args.json = False
    cmd_vet(args)


def cmd_settle(args):
    m = _mem(args)
    cp = record_outcome(m, args.address, args.category, args.outcome,
                        score=args.score, price=args.price, task=args.task, label=args.label)
    st = cp.get("stats", {})
    print(f"recorded {args.outcome} for {cp.get('label') or _short(args.address)} "
          f"· now {st.get('total')} jobs, {int((st.get('delivery_rate') or 0)*100)}% delivered")
    learned = synthesize_guardrails(m, args.category)
    if learned:
        print("reflection updated guardrails:", ", ".join(g["slug"] for g in learned))


def cmd_learn(args):
    m = _mem(args)
    cats = [args.category] if args.category else sorted(
        {c for cp in m.list_counterparties() for c in (cp.get("categories") or [])})
    total = []
    for cat in cats:
        for g in synthesize_guardrails(m, cat):
            total.append(g)
            print(f"⚑ [{cat}] {g['text']}")
    if not total:
        print("no new guardrails (not enough evidence yet)")


def cmd_roster(args):
    m = _mem(args)
    cps = sorted(m.list_counterparties(), key=lambda c: -(c.get("stats", {}).get("total") or 0))
    if not cps:
        print("roster is empty. run `priors seed` for the demo history.")
        return
    for cp in cps:
        st = cp.get("stats", {})
        v = vet(m, cp["address"], (cp.get("categories") or ["image"])[0])
        badge = _c(v.headline().ljust(8), _ACTION_COLOR[v.action])
        print(f"{badge} {(cp.get('label') or '?').ljust(14)} {_short(cp['address'])}  "
              f"{st.get('total',0)} jobs · {int((st.get('delivery_rate') or 0)*100)}% · "
              f"avg {st.get('avg_score')}/10 · {','.join(cp.get('categories') or [])}")


def cmd_guardrails(args):
    m = _mem(args)
    gs = m.guardrails_for(args.category) if args.category else m.all_guardrails()
    if not gs:
        print("no guardrails learned yet.")
        return
    for g in gs:
        print(f"⚑ [{g.get('svc_category')}] {g.get('text')}")
        print(f"    evidence: n={g.get('evidence',{}).get('n')} "
              f"fail_rate={g.get('evidence',{}).get('fail_rate')}")


def cmd_onchain(args):
    m = _mem(args)
    from .onchain import cache_onchain_prior
    prof = cache_onchain_prior(m, args.address)
    print(json.dumps(prof, indent=2))


def cmd_attest(args):
    from .onchain import attest_outcome, EXPLORER
    res = attest_outcome(args.address, args.category, args.verdict, args.score, args.evidence,
                         private_key=None if not args.send else None)
    print(json.dumps(res, indent=2))
    if not res.get("sent"):
        print("\nprepared but not sent. " + res.get("status", ""))


def cmd_snapshot(args):
    m = _mem(args)
    data = build_snapshot(m)
    out = args.out or "dashboard/data.json"
    import os
    d = os.path.dirname(out)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(out, "w") as f:
        json.dump(data, f, indent=2, default=str)
    # also emit a JS module so the static page works with no fetch (file:// too)
    js_path = os.path.join(d, "data.js") if d else "data.js"
    with open(js_path, "w") as f:
        f.write("window.PRIORS_DATA = " + json.dumps(data, default=str) + ";\n")
    m.set_snapshot({"generated_at": data["generated_at"], "counterparties": len(data["roster"])})
    print(f"wrote {out} and {js_path}  "
          f"({len(data['roster'])} counterparties, {len(data['guardrails'])} guardrails)")


def cmd_demo(args):
    """The fresh-session recall beat: the same question, cold vs warm."""
    import tempfile, os
    addr = args.address
    cat = args.category
    warm = _mem(args)
    if not warm.list_counterparties():
        seedmod.seed(warm, reset=False)
    cold = Memory(os.path.join(tempfile.mkdtemp(), "empty.db"))

    print(_c("── fresh session, empty memory ──────────────────────────────", "2"))
    print(render_verdict(vet(cold, addr, cat)))
    print()
    print(_c("── same agent, with its memory ──────────────────────────────", "2"))
    print(render_verdict(vet(warm, addr, cat)))
    print()
    print(_c("Delete the memory (priors forget) and it returns to the first answer.", "2"))


def cmd_forget(args):
    m = _mem(args)
    p = m.db_path
    m.wipe()
    print(f"deleted the memory layer at {p}")
    print("every counterparty is a stranger again. Priors now transacts blind.")


def build_snapshot(m: Memory) -> dict[str, Any]:
    from datetime import datetime, timezone
    roster = []
    for cp in m.list_counterparties():
        cats = cp.get("categories") or ["image"]
        v = vet(m, cp["address"], cats[0])
        st = cp.get("stats", {})
        roster.append({
            "label": cp.get("label"),
            "address": cp["address"],
            "categories": cats,
            "verdict": v.action,
            "confidence": v.confidence,
            "trust": v.score,
            "jobs": st.get("total", 0),
            "delivery_rate": st.get("delivery_rate"),
            "avg_score": st.get("avg_score"),
            "avg_price": st.get("avg_price"),
            "last_outcome": cp.get("last_outcome"),
            "onchain": cp.get("onchain"),
            "reason": v.reasons[0].text if v.reasons else "",
        })
    roster.sort(key=lambda r: (-{"avoid": 0, "caution": 1, "transact": 2}[r["verdict"]], -(r["jobs"] or 0)))
    recent = m.outcomes_for(limit=40)[:40]
    return {
        "product": "Priors",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "synthetic": True,
        "stats": m.stats(),
        "roster": roster,
        "guardrails": m.all_guardrails(),
        "recent_outcomes": recent,
    }


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="priors", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", help="path to the memory database (default: $PRIORS_DB or ./.priors/memory.db)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("db").set_defaults(func=cmd_db)

    s = sub.add_parser("seed"); s.add_argument("--reset", action="store_true"); s.set_defaults(func=cmd_seed)

    s = sub.add_parser("vet")
    s.add_argument("address"); s.add_argument("--category", default="image")
    s.add_argument("--price", type=float, default=None)
    s.add_argument("--onchain", action="store_true"); s.add_argument("--explain", action="store_true")
    s.add_argument("--json", action="store_true"); s.set_defaults(func=cmd_vet)

    s = sub.add_parser("why"); s.add_argument("address"); s.add_argument("--category", default="image")
    s.set_defaults(func=cmd_why)

    s = sub.add_parser("settle")
    s.add_argument("address"); s.add_argument("--category", default="image")
    s.add_argument("--outcome", required=True, choices=["delivered", "late", "partial", "stiffed"])
    s.add_argument("--score", type=float, default=None); s.add_argument("--price", type=float, default=None)
    s.add_argument("--label", default=None); s.add_argument("--task", default=None)
    s.set_defaults(func=cmd_settle)

    s = sub.add_parser("learn"); s.add_argument("--category", default=None); s.set_defaults(func=cmd_learn)
    sub.add_parser("roster").set_defaults(func=cmd_roster)
    sub.add_parser("ledger").set_defaults(func=cmd_roster)
    s = sub.add_parser("guardrails"); s.add_argument("--category", default=None); s.set_defaults(func=cmd_guardrails)

    s = sub.add_parser("onchain"); s.add_argument("address"); s.set_defaults(func=cmd_onchain)

    s = sub.add_parser("attest")
    s.add_argument("address"); s.add_argument("--category", default="image")
    s.add_argument("--verdict", required=True); s.add_argument("--score", type=int, default=0)
    s.add_argument("--evidence", default=""); s.add_argument("--send", action="store_true")
    s.set_defaults(func=cmd_attest)

    s = sub.add_parser("snapshot"); s.add_argument("--out", default=None); s.set_defaults(func=cmd_snapshot)

    s = sub.add_parser("demo"); s.add_argument("address", nargs="?",
                                               default="0xbad0000000000000000000000000000000000002")
    s.add_argument("--category", default="image"); s.set_defaults(func=cmd_demo)

    sub.add_parser("forget").set_defaults(func=cmd_forget)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
