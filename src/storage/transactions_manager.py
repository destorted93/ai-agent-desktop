"""Transactions ledger (mini-git index) per user session.

FsRevisionStore is the blob/manifest store + undo engine.
TransactionsManager is the authoritative index that answers:
- which txn_ids belong to which session entry tail / run
- and enforces exactly-once undo tracking

Design goals (v1):
- Simple, encrypted, per-session store
- Idempotent linking: txn_id is the primary key
- Deterministic ordering via monotonic seq
- Query by entry_id list (tail deletion) and by run_id (run diffs)

NOTE: v1 links transactions when main-session entries are persisted.
We do NOT ingest from participant/subagent stores (they are mirrors).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .secure import get_app_data_dir, read_encrypted_json, write_encrypted_json


def _now_utc_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


@dataclass
class TxnLinkResult:
    created: int = 0
    linked: int = 0


class TransactionsManager:
    """Per-session transactions ledger."""

    SCHEMA_VERSION = 1

    def __init__(self, base_dir: Optional[Path] = None) -> None:
        self._lock = threading.RLock()
        self.base_dir = base_dir or (get_app_data_dir() / "transactions")
        self.base_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------
    # Storage
    # ------------------------------

    def _path(self, session_id: str) -> Path:
        sid = str(session_id).strip()
        return self.base_dir / f"{sid}.enc"

    def _new_store(self, session_id: str) -> Dict[str, Any]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "session_id": str(session_id),
            "next_seq": 1,
            # txn_id -> record
            "txns": {},
            # entry_id -> [txn_id,...] (order = link order)
            "entry_index": {},
            # run_id -> [txn_id,...]
            "run_index": {},
        }

    def _load(self, session_id: str) -> Dict[str, Any]:
        p = self._path(session_id)
        data = read_encrypted_json(p)
        if isinstance(data, dict) and str(data.get("session_id") or "") == str(session_id):
            # Defensive upgrades
            if not isinstance(data.get("txns"), dict):
                data["txns"] = {}
            if not isinstance(data.get("entry_index"), dict):
                data["entry_index"] = {}
            if not isinstance(data.get("run_index"), dict):
                data["run_index"] = {}
            if not isinstance(data.get("next_seq"), int):
                data["next_seq"] = 1
            return data
        return self._new_store(session_id)

    def _save(self, session_id: str, store: Dict[str, Any]) -> None:
        p = self._path(session_id)
        write_encrypted_json(p, store)

    # ------------------------------
    # Linking
    # ------------------------------

    def link_txns_to_entry(
        self,
        *,
        session_id: str,
        entry_id: str,
        txn_ids: List[str],
        run_id: Optional[str] = None,
        actor: Optional[str] = None,
    ) -> TxnLinkResult:
        """Link txn_ids to a main session wrapped entry.

        Rules:
        - txn_id is the primary key.
        - First link wins for (txn_id -> entry_id/run_id/actor); we do not overwrite.
        - entry_index/run_index are append-only-ish; duplicates are deduped.
        """
        with self._lock:
            sid = str(session_id).strip()
            eid = str(entry_id).strip()
            if not sid:
                raise ValueError("session_id is required")
            if not eid:
                raise ValueError("entry_id is required")

            txs = [str(t).strip() for t in (txn_ids or []) if isinstance(t, str) and str(t).strip()]
            if not txs:
                return TxnLinkResult(created=0, linked=0)

            store = self._load(sid)
            txns = store.get("txns") if isinstance(store.get("txns"), dict) else {}
            entry_index = store.get("entry_index") if isinstance(store.get("entry_index"), dict) else {}
            run_index = store.get("run_index") if isinstance(store.get("run_index"), dict) else {}

            created = 0
            linked = 0

            # Ensure entry_index list exists
            lst = entry_index.get(eid)
            if not isinstance(lst, list):
                lst = []
                entry_index[eid] = lst

            for txn_id in txs:
                rec = txns.get(txn_id)
                if not isinstance(rec, dict):
                    seq = int(store.get("next_seq") or 1)
                    store["next_seq"] = seq + 1
                    rec = {
                        "txn_id": str(txn_id),
                        "seq": seq,
                        "linked_at": _now_utc_iso(),
                        "entry_id": eid,
                        "run_id": (str(run_id) if isinstance(run_id, str) and run_id else None),
                        "actor": (str(actor) if isinstance(actor, str) and actor else None),
                        "status": "committed",
                        "undone_at": None,
                        "undo_txn_id": None,
                    }
                    txns[txn_id] = rec
                    created += 1
                else:
                    # First link wins for entry_id, but we allow backfilling missing run_id/actor.
                    try:
                        if (not rec.get("run_id")) and isinstance(run_id, str) and run_id:
                            rec["run_id"] = str(run_id)
                        if (not rec.get("actor")) and isinstance(actor, str) and actor:
                            rec["actor"] = str(actor)
                        txns[txn_id] = rec
                    except Exception:
                        pass

                # entry_index: keep link order, dedupe
                if txn_id not in lst:
                    lst.append(txn_id)
                    linked += 1

                # run_index: only if run_id is present and record has a run_id
                rid = rec.get("run_id")
                if isinstance(rid, str) and rid:
                    rl = run_index.get(rid)
                    if not isinstance(rl, list):
                        rl = []
                        run_index[rid] = rl
                    if txn_id not in rl:
                        rl.append(txn_id)

            store["txns"] = txns
            store["entry_index"] = entry_index
            store["run_index"] = run_index
            self._save(sid, store)
            return TxnLinkResult(created=created, linked=linked)

    # ------------------------------
    # Query
    # ------------------------------

    def get_txn_ids_for_entry_ids(self, *, session_id: str, entry_ids: List[str]) -> List[str]:
        """Return txn_ids linked to entry_ids, in the order entries are provided.

        This is intentionally NOT sorted by time: the caller passes a tail in main-log order.
        Undo should usually reverse this list (newest->oldest).
        """
        with self._lock:
            sid = str(session_id).strip()
            if not sid:
                return []
            store = self._load(sid)
            entry_index = store.get("entry_index") if isinstance(store.get("entry_index"), dict) else {}

            out: List[str] = []
            seen = set()
            for eid in (entry_ids or []):
                if not isinstance(eid, str) or not eid:
                    continue
                txs = entry_index.get(eid)
                if not isinstance(txs, list):
                    continue
                for t in txs:
                    if isinstance(t, str) and t and t not in seen:
                        seen.add(t)
                        out.append(t)
            return out

    def get_txn_ids_for_run(self, *, session_id: str, run_id: str) -> List[str]:
        with self._lock:
            sid = str(session_id).strip()
            rid = str(run_id).strip()
            if not sid or not rid:
                return []
            store = self._load(sid)
            run_index = store.get("run_index") if isinstance(store.get("run_index"), dict) else {}
            txs = run_index.get(rid)
            if not isinstance(txs, list):
                return []
            # Dedupe in order
            out: List[str] = []
            seen = set()
            for t in txs:
                if isinstance(t, str) and t and t not in seen:
                    seen.add(t)
                    out.append(t)
            return out

    def get_txn_ids_for_entry_id(self, *, session_id: str, entry_id: str) -> List[str]:
        return self.get_txn_ids_for_entry_ids(session_id=session_id, entry_ids=[entry_id])

    def get_txn_map_for_entry_ids(self, *, session_id: str, entry_ids: List[str]) -> Dict[str, List[str]]:
        """Return mapping entry_id -> txn_ids (order preserved per entry)."""
        with self._lock:
            sid = str(session_id).strip()
            if not sid:
                return {}
            store = self._load(sid)
            entry_index = store.get("entry_index") if isinstance(store.get("entry_index"), dict) else {}
            out: Dict[str, List[str]] = {}
            for eid in entry_ids or []:
                if not isinstance(eid, str) or not eid:
                    continue
                txs = entry_index.get(eid)
                if isinstance(txs, list):
                    out[eid] = [str(t) for t in txs if isinstance(t, str) and t]
            return out


    def list_transactions_for_session(
        self,
        *,
        session_id: str,
        limit: int = 20,
        include_undone: bool = True,
    ) -> List[Dict[str, Any]]:
        """List transaction records linked to a given session.

        This is the session-scoped view used by fs_list_transactions. It intentionally
        does NOT read the global FsRevisionStore index.

        Ordering: oldest -> newest (by monotonic seq).
        """
        with self._lock:
            sid = str(session_id).strip()
            if not sid:
                return []

            try:
                lim = int(limit)
            except Exception:
                lim = 20
            if lim <= 0:
                lim = 20

            store = self._load(sid)
            txns = store.get("txns") if isinstance(store.get("txns"), dict) else {}

            recs: List[Dict[str, Any]] = []
            for rec in txns.values():
                if not isinstance(rec, dict):
                    continue
                if (not include_undone) and str(rec.get("status") or "") == "undone":
                    continue
                recs.append(dict(rec))

            def _seq(r: Dict[str, Any]) -> int:
                try:
                    return int(r.get("seq") or 0)
                except Exception:
                    return 0

            recs.sort(key=_seq)
            if lim and len(recs) > lim:
                recs = recs[-lim:]
            return recs
    # ------------------------------
    # Undo tracking
    # ------------------------------

    def is_undone(self, *, session_id: str, txn_id: str) -> bool:
        with self._lock:
            sid = str(session_id).strip()
            tid = str(txn_id).strip()
            if not sid or not tid:
                return False
            store = self._load(sid)
            txns = store.get("txns") if isinstance(store.get("txns"), dict) else {}
            rec = txns.get(tid)
            if not isinstance(rec, dict):
                return False
            return str(rec.get("status") or "") == "undone"

    def mark_undone(
        self,
        *,
        session_id: str,
        txn_id: str,
        undo_txn_id: Optional[str] = None,
        undone_at: Optional[str] = None,
    ) -> None:
        with self._lock:
            sid = str(session_id).strip()
            tid = str(txn_id).strip()
            if not sid or not tid:
                return
            store = self._load(sid)
            txns = store.get("txns") if isinstance(store.get("txns"), dict) else {}
            rec = txns.get(tid)
            if not isinstance(rec, dict):
                return
            rec["status"] = "undone"
            rec["undone_at"] = str(undone_at) if isinstance(undone_at, str) and undone_at else _now_utc_iso()
            if isinstance(undo_txn_id, str) and undo_txn_id:
                rec["undo_txn_id"] = str(undo_txn_id)
            txns[tid] = rec
            store["txns"] = txns
            self._save(sid, store)

    def delete_session_ledger(self, *, session_id: str) -> None:
        """Delete the ledger file for a session (best-effort)."""
        try:
            p = self._path(str(session_id))
            if p.exists():
                p.unlink()
        except Exception:
            pass
