"""A disclosed, synthetic demo history.

None of this is real trading data. It is a hand-built roster of image-generation
counterparties, backdated across several sessions, so the dashboard and the
demo have something to show. Every command that surfaces seeded data labels it
as synthetic. The eligibility-gate tests do not use the seed: they build their
own history from empty.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any

from .memory import Memory
from .learn import record_outcome, synthesize_guardrails
from .engine import DEFAULT_POLICY

_NOW = datetime.now(timezone.utc)


def _ts(days_ago: float) -> str:
    return (_NOW - timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")


# (label, address, category, [(outcome, score, price, days_ago, task)])
ROSTER: list[tuple[str, str, str, list[tuple]]] = [
    ("MemeMaster", "0xa11ce00000000000000000000000000000000001", "image", [
        ("delivered", 9, 2.0, 8, "launch meme"),
        ("delivered", 10, 2.0, 6, "mascot sticker set"),
        ("delivered", 8, 2.0, 3, "banner variations"),
        ("delivered", 9, 2.0, 1, "reaction pack"),
    ]),
    ("PixelBot", "0xbad0000000000000000000000000000000000002", "image", [
        ("stiffed", 0, 1.0, 7, "logo draft"),
        ("stiffed", 0, 1.0, 4, "retry logo"),
    ]),
    ("QuickArt", "0xc0ffee0000000000000000000000000000000003", "image", [
        ("delivered", 7, 2.5, 6, "thumbnail"),
        ("late", 6, 2.5, 4, "carousel set"),
        ("partial", 4, 2.5, 2, "icon sheet"),
    ]),
    ("HiRes Studio", "0xf00d000000000000000000000000000000000004", "image", [
        ("delivered", 9, 5.0, 5, "hero illustration"),
        ("delivered", 10, 5.0, 3, "print poster"),
        ("delivered", 9, 5.0, 1, "cover art"),
    ]),
    ("BudgetPix", "0x0000000000000000000000000000000000000ba1", "image", [
        ("stiffed", 0, 0.5, 7, "quick meme")]),
    ("CheapFrames", "0x0000000000000000000000000000000000000ba2", "image", [
        ("partial", 3, 0.5, 6, "sticker")]),
    ("QuickCheap", "0x0000000000000000000000000000000000000ba3", "image", [
        ("stiffed", 0, 0.6, 5, "avatar")]),
    ("PennyArt", "0x0000000000000000000000000000000000000ba4", "image", [
        ("delivered", 4, 0.5, 4, "banner")]),
    ("LowRes", "0x0000000000000000000000000000000000000ba5", "image", [
        ("stiffed", 0, 0.6, 3, "logo")]),
    ("BargainBot", "0x0000000000000000000000000000000000000ba6", "image", [
        ("partial", 2, 0.7, 2, "icon")]),
    # a second category, to show the roster spans skills
    ("CopyCrafter", "0x0000000000000000000000000000000000000c01", "copy", [
        ("delivered", 8, 1.0, 5, "tagline"),
        ("delivered", 9, 1.0, 2, "product blurb"),
    ]),
]

POLICY = {
    **DEFAULT_POLICY,
    "avoid_if_stiffed_gte": 2,
    "caution_if_delivery_rate_lt": 0.75,
}


def seed(mem: Memory, reset: bool = False) -> dict[str, Any]:
    if reset:
        mem.reset()
    mem.set_policy(POLICY)
    mem.set_focus({"task": "vet counterparties for image-generation jobs", "seeded": True})

    n_out = 0
    for label, addr, cat, rows in ROSTER:
        for outcome, score, price, days_ago, task in rows:
            record_outcome(mem, addr, cat, outcome, score=score, price=price,
                           task=task, label=label, ts=_ts(days_ago))
            n_out += 1

    learned = []
    for cat in ("image", "copy"):
        learned += synthesize_guardrails(mem, cat)

    return {
        "counterparties": len({r[1] for r in ROSTER}),
        "outcomes": n_out,
        "guardrails": [g["slug"] for g in learned],
        "synthetic": True,
    }
