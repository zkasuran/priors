"""Optional natural-language rationale for a verdict.

This phrases a verdict for a human. It never changes the decision, and every
number it uses comes from the verdict that memory already produced. If no LLM
is configured it falls back to the deterministic reasons, so the tool works
fully offline.
"""

from __future__ import annotations

import os

from .engine import Verdict


def _fallback(v: Verdict) -> str:
    lines = [f"{v.headline()} (confidence {v.confidence}). "]
    lines += [f"- {r.text}" for r in v.reasons]
    if v.alternative:
        lines.append(f"- Alternative: {v.alternative.get('label') or v.alternative.get('address')}")
    return "\n".join(lines)


def explain(v: Verdict, model: str | None = None) -> str:
    """Return a one-paragraph rationale. Uses Anthropic if available, else the
    deterministic reasons. Any error degrades to the fallback."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return _fallback(v)
    try:
        import anthropic

        facts = {
            "verdict": v.action,
            "confidence": v.confidence,
            "trust_score": v.score,
            "counterparty": v.priors_used.get("label") or v.address,
            "category": v.svc_category,
            "reasons": [r.text for r in v.reasons],
            "aggregate": v.priors_used.get("aggregate"),
            "terms": v.terms,
            "alternative": v.alternative,
        }
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model=model or os.environ.get("ANTHROPIC_MODEL") or "claude-sonnet-5",
            max_tokens=220,
            system=(
                "You explain a vetting agent's decision to its operator in two or three "
                "plain sentences. Use only the facts given. Do not invent numbers. Do not "
                "use em dashes. Do not add a comma before 'and' or 'or'. Be direct."
            ),
            messages=[{"role": "user", "content": f"Explain this verdict:\n{facts}"}],
        )
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
        return text or _fallback(v)
    except Exception:
        return _fallback(v)
