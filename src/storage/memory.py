"""User memory persistence with encryption.

This module stores long-term "memories" as encrypted JSON.

2026-03: We support per-agent memory stores (e.g., Aria vs Ariane) by routing
MemoryManager's default file path through appcore RunContext (ambient per-run context).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Optional, List, Dict, Any
import uuid

from .secure import get_app_data_dir, write_encrypted_json, read_encrypted_json


# Valid memory categories (single source of truth)
MEMORY_CATEGORIES_ORDERED = ["user", "self", "relationship", "work"]
VALID_CATEGORIES = set(MEMORY_CATEGORIES_ORDERED)

# Category caps (None = uncapped)
# Phase 1: enforced in memory tools (not inside MemoryManager) so we can support
# partial-success create_memory calls with clean errors.
MEMORY_CATEGORY_CAPS: Dict[str, Optional[int]] = {
    "user": 30,
    "self": 30,
    "relationship": 60,
    "work": None,
}

# Note: we intentionally avoid duplicating categories in a typing Literal.
# MEMORY_CATEGORIES_ORDERED is the single source of truth.


# -----------------------------------------------------------------------------
# Per-agent routing
# -----------------------------------------------------------------------------

# Default agent routing uses appcore RunContext (set by the app runner around Agent.run()).
# This keeps the agent loop/tool execution agnostic.


def _normalize_agent_id(agent_id: Optional[str]) -> Optional[str]:
    """Normalize an agent id into a filesystem-safe slug.

    Keep it intentionally strict: [a-z0-9_-].
    """
    if not isinstance(agent_id, str):
        return None
    s = agent_id.strip().lower()
    if not s:
        return None
    out: List[str] = []
    for ch in s:
        if ch.isalnum() or ch in ("_", "-"):
            out.append(ch)
        elif ch.isspace() or ch in ("/", "\\", "."):
            out.append("_")
        else:
            # drop other punctuation
            continue
    slug = "".join(out).strip("_")
    return slug or None


def get_current_memory_agent_id() -> Optional[str]:
    """Return the current agent id for memory routing (from RunContext)."""
    try:
        from ..appcore.run_context import get_run_context

        ctx = get_run_context()
        return _normalize_agent_id(getattr(ctx, "agent_id", None))
    except Exception:
        return None


def _default_memories_dir() -> Path:
    return get_app_data_dir() / "Memories"


def get_legacy_memory_path() -> Path:
    """Legacy pre-per-agent memory file location."""
    return get_app_data_dir() / "memories.enc"


def _default_memory_path_for_agent(agent_id: Optional[str]) -> Path:
    """Compute the default encrypted file path for an agent's memory store.

    Back-compat: if the per-agent file doesn't exist yet and agent_id == "aria",
    we fall back to the legacy `memories.enc` at app root.
    """
    aid = _normalize_agent_id(agent_id)

    legacy = get_legacy_memory_path()

    if not aid:
        # Legacy behavior (should be rare now that the runner sets RunContext).
        return legacy

    memories_dir = _default_memories_dir()
    memories_dir.mkdir(parents=True, exist_ok=True)

    per_agent = memories_dir / f"memories_{aid}.enc"

    # Back-compat / migration helper: keep Aria alive if the user hasn't moved the file yet.
    if aid == "aria" and (not per_agent.exists()) and legacy.exists():
        return legacy

    return per_agent


def list_memory_stores(include_legacy: bool = True) -> List[Dict[str, Any]]:
    """List available memory stores.

    Returns a list of dicts:
      - agent_id
      - file_name
      - abs_path
      - modified_ts (float unix seconds) or None
    """
    stores: List[Dict[str, Any]] = []

    base = _default_memories_dir()
    try:
        base.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    # Prefer a stable ordering: aria, ariane, then everything else.
    def _sort_key(p: Path) -> tuple:
        name = p.name
        aid = None
        if name.startswith("memories_") and name.endswith(".enc"):
            aid = name[len("memories_") : -len(".enc")].strip() or None
        aid = (aid or "").lower()
        rank = 10
        if aid == "aria":
            rank = 0
        elif aid == "ariane":
            rank = 1
        return (rank, aid, name)

    try:
        for p in sorted(base.glob("memories_*.enc"), key=_sort_key):
            name = p.name
            agent_id = None
            if name.startswith("memories_") and name.endswith(".enc"):
                agent_id = name[len("memories_") : -len(".enc")].strip() or None
            agent_id = _normalize_agent_id(agent_id)
            if not agent_id:
                continue
            try:
                mtime = p.stat().st_mtime
            except Exception:
                mtime = None

            stores.append(
                {
                    "agent_id": str(agent_id),
                    "file_name": str(p.name),
                    "abs_path": str(p),
                    "modified_ts": float(mtime) if isinstance(mtime, (int, float)) else None,
                }
            )
    except Exception:
        pass

    if include_legacy:
        try:
            legacy = get_legacy_memory_path()
            if legacy.exists():
                try:
                    mtime = legacy.stat().st_mtime
                except Exception:
                    mtime = None
                stores.append(
                    {
                        "agent_id": "legacy",
                        "file_name": "memories.enc",
                        "abs_path": str(legacy),
                        "modified_ts": float(mtime) if isinstance(mtime, (int, float)) else None,
                    }
                )
        except Exception:
            pass

    return stores


# -----------------------------------------------------------------------------
# Light concurrency hygiene
# -----------------------------------------------------------------------------

_file_locks_guard = RLock()
_file_locks: Dict[str, RLock] = {}


def _lock_for(path: Path) -> RLock:
    key = str(path)
    with _file_locks_guard:
        lk = _file_locks.get(key)
        if lk is None:
            lk = RLock()
            _file_locks[key] = lk
        return lk


class MemoryManager:
    """Manages encrypted user memory persistence."""

    def __init__(self, file_path: Optional[Path] = None, *, agent_id: Optional[str] = None):
        """Initialize the memory manager.

        Args:
            file_path: Custom path for memory file (overrides routing)
            agent_id: Agent id to route to (defaults to RunContext.agent_id)
        """
        if file_path is None:
            if agent_id is None:
                agent_id = get_current_memory_agent_id()
            file_path = _default_memory_path_for_agent(agent_id)

        self.file_path = Path(file_path)
        self.memories: List[Dict] = []
        self.load()

    def load(self) -> None:
        """Load memories from encrypted file.

        Migration: older stores used sequential numeric string ids ("1", "2", ...).
        We now use stable UUID ids so external indexes (e.g. vector search) don't break.
        """
        lk = _lock_for(self.file_path)
        with lk:
            data = read_encrypted_json(self.file_path)
            memories = data if isinstance(data, list) else []

            migrated = False
            for m in memories:
                if not isinstance(m, dict):
                    continue
                mid = m.get("id")
                is_legacy = False
                if mid is None:
                    is_legacy = True
                elif isinstance(mid, int):
                    is_legacy = True
                elif isinstance(mid, str) and mid.strip().isdigit():
                    is_legacy = True

                if is_legacy:
                    if mid is not None and "legacy_id" not in m:
                        m["legacy_id"] = str(mid)
                    m["id"] = str(uuid.uuid4())
                    migrated = True

            self.memories = memories

            # Persist migration under the same lock.
            if migrated:
                try:
                    write_encrypted_json(self.file_path, self.memories)
                except Exception:
                    # If this fails, we still keep the in-memory migrated ids.
                    pass

    def save(self) -> Dict[str, Any]:
        """Save memories to encrypted file."""
        lk = _lock_for(self.file_path)
        with lk:
            try:
                write_encrypted_json(self.file_path, self.memories)
                return {"status": "success"}
            except Exception as e:
                return {"status": "error", "message": str(e)}

    def get_memories(self) -> List[Dict]:
        """Get all memories."""
        return self.memories

    def get_memories_with_stats(self) -> Dict[str, Any]:
        """Get all memories with category statistics.

        Returns:
            Dict with 'memories' list and 'stats' showing count per category
        """
        stats = {c: 0 for c in MEMORY_CATEGORIES_ORDERED}
        for memory in self.memories:
            category = memory.get("category", "user")
            if category in stats:
                stats[category] += 1
        return {
            "memories": self.memories,
            "stats": stats,
            "total": len(self.memories),
        }

    def add_memory(self, text: str, category: str = "user") -> Dict[str, Any]:
        """Add a new memory.

        Args:
            text: Memory content
            category: One of 'user', 'self', or 'relationship'
        """
        try:
            # Validate category
            if category not in VALID_CATEGORIES:
                return {
                    "status": "error",
                    "message": f"Invalid category '{category}'. Must be one of: {', '.join(VALID_CATEGORIES)}",
                }

            # Reload under lock to reduce lost updates when multiple MemoryManager instances exist.
            self.load()

            new_id = str(uuid.uuid4())
            now = datetime.now()
            memory = {
                "id": new_id,
                "category": category,
                "date": now.strftime("%Y-%m-%d"),
                "time": now.strftime("%H:%M"),
                "text": text,
            }
            self.memories.append(memory)
            result = self.save()
            if result["status"] == "success":
                # Don’t echo memory text back in tool results (saves context tokens).
                memory_meta = {
                    "id": memory["id"],
                    "category": memory["category"],
                    "date": memory["date"],
                    "time": memory["time"],
                }
                return {"status": "success", "id": new_id, "memory": memory_meta}
            return {"status": "error", "message": result.get("message", "Failed to save")}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def update_memory(
        self,
        memory_id: str,
        new_text: Optional[str] = None,
        new_category: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Update an existing memory.

        Args:
            memory_id: ID of memory to update
            new_text: New text content (optional)
            new_category: New category (optional)
        """
        # Validate category if provided
        if new_category is not None and new_category not in VALID_CATEGORIES:
            return {
                "status": "error",
                "id": memory_id,
                "message": f"Invalid category '{new_category}'. Must be one of: {', '.join(VALID_CATEGORIES)}",
            }

        # Reload under lock to reduce lost updates when multiple MemoryManager instances exist.
        self.load()

        for memory in self.memories:
            if memory["id"] == memory_id:
                if new_text is not None:
                    memory["text"] = new_text
                if new_category is not None:
                    memory["category"] = new_category
                result = self.save()
                if result["status"] == "success":
                    # Don’t echo memory text back in tool results (saves context tokens).
                    memory_meta = {
                        "id": memory["id"],
                        "category": memory["category"],
                        "date": memory.get("date"),
                        "time": memory.get("time"),
                    }
                    return {"status": "success", "id": memory_id, "memory": memory_meta}
                return {"status": "error", "id": memory_id, "message": result.get("message")}
        return {"status": "error", "id": memory_id, "message": "Memory not found"}

    def delete_memories(self, ids: List[str]) -> List[Dict[str, Any]]:
        """Delete memories by IDs."""
        # Reload under lock to reduce lost updates when multiple MemoryManager instances exist.
        self.load()

        found = {id_ for id_ in ids if any(m["id"] == id_ for m in self.memories)}
        self.memories = [m for m in self.memories if m["id"] not in found]

        # Do NOT renumber ids (UUIDs must be stable for external references).
        result = self.save()
        results = []
        for id_ in ids:
            if id_ in found:
                if result["status"] == "success":
                    results.append({"status": "success", "id": id_})
                else:
                    results.append({"status": "error", "id": id_, "message": result.get("message")})
            else:
                results.append({"status": "error", "id": id_, "message": "Memory not found"})
        return results

    def clear(self) -> Dict[str, Any]:
        """Clear all memories."""
        self.memories = []
        return self.save()
