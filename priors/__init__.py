"""Priors: an agent that turns every outcome into a verifiable prior.

The whole product is its memory. `priors.memory.Memory` is the Sibyl-backed
spine; `priors.engine` reads it to reach a verdict; `priors.learn` writes
outcomes back and synthesizes the guardrails that change future verdicts.
Delete the memory and Priors has no priors: it transacts blind. That collapse
is the eligibility gate (tests/test_gate.py).
"""

__version__ = "0.1.0"
