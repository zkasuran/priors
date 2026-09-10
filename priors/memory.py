"""The Priors memory spine, built on Sibyl Memory (local-first SQLite + FTS5, MIT).

Every verdict Priors reaches reads from here, and every graded outcome is
written back here. Delete this store and Priors has no priors: it treats every
counterparty as a stranger and transacts blind. That collapse is the
eligibility gate, asserted in tests/test_gate.py.

Sibyl's five tiers, each doing a specific job for us:

  WARM  entities   the roster (one record per counterparty) + learned
                   guardrails                              set/get/list_entities
  COLD  journal    every graded outcome, append-only, time-ordered, so the
                   record compounds across sessions        write/read_events
  REFERENCE        the static vetting policy the engine starts from  set/get_reference
  HOT   state      the agent's working focus + last dashboard snapshot  set/get_state

FTS5 search() spans the tiers for "have I dealt with anything like this".
This module speaks the domain (counterparties, outcomes, guardrails, policy)
so the engine and the learner never touch Sibyl directly.
"""

from __future__ import annotations

import os
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from sibyl_memory_client import MemoryClient

try:  # NotFoundError location may vary across client versions
    from sibyl_memory_client.exceptions import NotFoundError
except Exception:  # pragma: no cover
    class NotFoundError(Exception):
        pass

# Sibyl entity categories and the fixed keys we reserve.
CAT_COUNTERPARTY = "counterparty"
CAT_GUARDRAIL = "guardrail"
POLICY_KEY = "vetting-policy"
FOCUS_KEY = "focus"
SNAPSHOT_KEY = "dashboard-snapshot"

DEFAULT_DB = os.path.join(os.getcwd(), ".priors", "memory.db")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def norm_addr(address: str) -> str:
    """Counterparties are keyed by a normalized id (a lowercased address or handle)."""
    return (address or "").strip().lower()


class Memory:
    """Domain wrapper over a single local Sibyl Memory database."""

    def __init__(self, db_path: str | None = None):
        self.db_path = os.path.abspath(
            db_path or os.environ.get("PRIORS_DB") or DEFAULT_DB
        )
        Path(os.path.dirname(self.db_path)).mkdir(parents=True, exist_ok=True)
        self._client = MemoryClient.local(self.db_path)

    @property
    def client(self) -> MemoryClient:
        return self._client

    # ---- lifecycle -------------------------------------------------------
    def exists(self) -> bool:
        return os.path.exists(self.db_path)

    def wipe(self) -> None:
        """Delete the whole store. This is the "delete the memory layer" beat.

        After this, a re-opened Memory has nothing: no roster, no outcomes, no
        guardrails. The engine falls back to transacting blind.
        """
        for suffix in ("", "-wal", "-shm"):
            p = self.db_path + suffix
            if os.path.exists(p):
                os.remove(p)

    # ---- WARM: the roster ------------------------------------------------
    def get_counterparty(self, address: str) -> dict[str, Any] | None:
        try:
            row = self._client.get_entity(CAT_COUNTERPARTY, norm_addr(address))
        except NotFoundError:
            return None
        return (row or {}).get("body") if row else None

    def put_counterparty(self, address: str, body: dict[str, Any]) -> None:
        self._client.set_entity(CAT_COUNTERPARTY, norm_addr(address), body)

    def list_counterparties(self, limit: int = 200) -> list[dict[str, Any]]:
        rows = self._client.list_entities(CAT_COUNTERPARTY, limit=limit) or []
        return [r.get("body", {}) for r in rows if r.get("body")]

    # ---- COLD: the outcome ledger ---------------------------------------
    def record_outcome_event(self, payload: dict[str, Any], ts: str | None = None) -> str:
        """Append one graded outcome to the journal. Append-only by design."""
        return self._client.write_event(
            acted=payload,
            extra={
                "address": payload.get("address"),
                "svc_category": payload.get("svc_category"),
                "outcome": payload.get("outcome"),
            },
            ts=ts or payload.get("ts") or now_iso(),
        )

    def outcomes_for(
        self,
        address: str | None = None,
        svc_category: str | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """Read graded outcomes, newest first, optionally filtered.

        Each journal row carries our payload under `acted`; we index by address
        and service category in `extra`, but filter on the payload itself so a
        row written by an older schema still resolves.
        """
        events = self._client.read_events(limit=limit) or []
        want_addr = norm_addr(address) if address else None
        out: list[dict[str, Any]] = []
        for ev in events:
            payload = ev.get("acted") or {}
            if not isinstance(payload, dict) or "outcome" not in payload:
                continue
            if want_addr and norm_addr(payload.get("address", "")) != want_addr:
                continue
            if svc_category and payload.get("svc_category") != svc_category:
                continue
            payload = dict(payload)
            payload.setdefault("ts", ev.get("ts") or ev.get("created_at"))
            out.append(payload)
        return out

    # ---- WARM: learned guardrails ---------------------------------------
    def put_guardrail(self, svc_category: str, slug: str, body: dict[str, Any]) -> None:
        self._client.set_entity(CAT_GUARDRAIL, f"{svc_category}/{slug}", body)

    def guardrails_for(self, svc_category: str) -> list[dict[str, Any]]:
        rows = self._client.list_entities(CAT_GUARDRAIL, limit=200) or []
        out = []
        prefix = f"{svc_category}/"
        for r in rows:
            if (r.get("name") or "").startswith(prefix) and r.get("body"):
                out.append(r["body"])
        return out

    def all_guardrails(self) -> list[dict[str, Any]]:
        rows = self._client.list_entities(CAT_GUARDRAIL, limit=200) or []
        return [r["body"] for r in rows if r.get("body")]

    # ---- REFERENCE: the vetting policy ----------------------------------
    def get_policy(self) -> dict[str, Any] | None:
        row = self._client.get_reference(POLICY_KEY)
        if not row:
            return None
        body = row.get("body") if isinstance(row, dict) else row
        if isinstance(body, str):  # reference bodies come back JSON-encoded
            try:
                body = json.loads(body)
            except (ValueError, TypeError):
                return None
        return body if isinstance(body, dict) else None

    def set_policy(self, body: dict[str, Any]) -> None:
        self._client.set_reference(POLICY_KEY, body, metadata={"kind": "vetting-policy"})

    # ---- HOT: working state ---------------------------------------------
    def set_focus(self, body: dict[str, Any]) -> None:
        self._client.set_state(FOCUS_KEY, body)

    def get_focus(self) -> dict[str, Any] | None:
        row = self._client.get_state(FOCUS_KEY)
        return (row.get("body") if isinstance(row, dict) else row) if row else None

    def set_snapshot(self, body: dict[str, Any]) -> None:
        self._client.set_state(SNAPSHOT_KEY, body)

    def get_snapshot(self) -> dict[str, Any] | None:
        row = self._client.get_state(SNAPSHOT_KEY)
        return (row.get("body") if isinstance(row, dict) else row) if row else None

    # ---- FTS5 across tiers ----------------------------------------------
    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        if len(query) < 3:
            return []
        return self._client.search(query, limit=limit) or []

    # ---- dashboard counts -----------------------------------------------
    def stats(self) -> dict[str, int]:
        cps = self._client.list_entities(CAT_COUNTERPARTY, limit=1000) or []
        grds = self._client.list_entities(CAT_GUARDRAIL, limit=1000) or []
        evs = self._client.read_events(limit=5000) or []
        return {
            "counterparties": len(cps),
            "guardrails": len(grds),
            "outcomes": len(evs),
        }
