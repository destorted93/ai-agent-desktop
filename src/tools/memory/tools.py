"""Memory management tools for the agent."""

from typing import List, Dict, Any
from ...storage import MemoryManager
from ...storage.memory import MEMORY_CATEGORIES_ORDERED, MEMORY_CATEGORY_CAPS


class GetMemoriesTool:
    """Tool to retrieve memories."""

    schema = {
        "type": "function",
        "name": "get_memories",
        "description": "Retrieve stored memories excluding category='work', with category statistics. Returns memories (id/category/text/timestamp) and stats showing count per category (user/self/relationship/work).",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    }

    def run(self, **kwargs) -> Dict[str, Any]:
        manager = MemoryManager()
        result = manager.get_memories_with_stats()

        # Phase 2: don't return 'work' memories here, and strip timestamp fields
        # from the tool output (we keep date/time in storage for human debugging).
        try:
            mems = result.get("memories") if isinstance(result, dict) else None
            if isinstance(mems, list):
                slim = []
                for m in mems:
                    if not isinstance(m, dict):
                        continue
                    if m.get("category") == "work":
                        continue
                    slim.append(
                        {
                            "id": m.get("id"),
                            "category": m.get("category"),
                            "text": m.get("text"),
                        }
                    )
                result["memories"] = slim
                result["total"] = len(slim)  # total returned
        except Exception:
            pass

        return {"status": "success", **result}


class SearchMemoriesTool:
    """Tool to search memories (semantic/embedding search planned)."""

    schema = {
        "type": "function",
        "name": "search_memories",
        "description": (
            "Search memories using semantic / embedding-based retrieval and return the most relevant entries. "
            "Provide a natural-language query and select which categories to search."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural-language search query.",
                },
                "categories": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": MEMORY_CATEGORIES_ORDERED,
                    },
                    "description": "Which memory categories to search.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 50,
                    "description": "Max number of results to return.",
                },
                "survive": {
                    "anyOf": [{"type": "boolean"}, {"type": "null"}],
                    "description": "If false, this call/output will be excluded from future agent context (UI/persistence unchanged). Null/omitted defaults to true.",
                },
            },
            "required": ["query", "categories", "limit", "survive"],
            "additionalProperties": False,
        },
    }

    def run(self, query: str, categories: List[str], limit: int, survive: Any = None) -> Dict[str, Any]:
        import hashlib

        from ...appcore.runtime_context import Runtime
        from ...storage.memory import get_current_memory_agent_id

        q = str(query or "").strip()
        if not q:
            return {"status": "error", "message": "query is required"}

        cats = [c for c in (categories or []) if isinstance(c, str) and c in MEMORY_CATEGORIES_ORDERED]
        if not cats:
            return {"status": "error", "message": "categories must be a non-empty array"}

        try:
            n = int(limit)
        except Exception:
            n = 5
        n = max(1, min(50, n))

        aid = get_current_memory_agent_id() or "legacy"
        collection_name = f"memories_{aid}"

        mgr = Runtime.get_memory_vectordb_manager()
        if not mgr:
            rag_mgr = Runtime.get_vectordb_manager()
            api_key = getattr(rag_mgr, "api_key", None) if rag_mgr else None
            base_url = getattr(rag_mgr, "base_url", None) if rag_mgr else None
            embedding_model = getattr(rag_mgr, "embedding_model", "text-embedding-3-small") if rag_mgr else "text-embedding-3-small"
            mgr = Runtime.init_memory_vectordb_manager(
                api_key=api_key,
                base_url=base_url,
                embedding_model=embedding_model,
            )

        if not mgr:
            msg = Runtime.get_context().memory_vectordb_init_error or "Memory VectorDB not initialized"
            return {"status": "error", "message": msg}

        if not getattr(mgr, "openai_client", None):
            return {
                "status": "error",
                "message": "OpenAI client not configured. Please set API key in Settings.",
            }

        # Ensure per-agent collection exists.
        try:
            collection = mgr.client.get_collection(name=collection_name)
        except Exception:
            mgr.create_collection(
                name=collection_name,
                description=f"Memory index ({aid})",
                source_type="memory",
                tags=["memory", aid],
            )
            collection = mgr.client.get_collection(name=collection_name)

        # Load source-of-truth memories (encrypted JSON) and index (embeddings-only) into Chroma.
        mem_mgr = MemoryManager(agent_id=aid)
        all_mems = mem_mgr.get_memories() or []
        all_by_id = {
            m.get("id"): m
            for m in all_mems
            if isinstance(m, dict) and isinstance(m.get("id"), str) and m.get("text")
        }

        # Only index selected categories.
        target_ids: List[str] = []
        target_texts: List[str] = []
        target_metas: List[Dict[str, Any]] = []

        for mid, m in all_by_id.items():
            try:
                cat = m.get("category")
                if cat not in cats:
                    continue
                text = str(m.get("text") or "")
                if not text.strip():
                    continue
                h = hashlib.sha256(text.encode("utf-8")).hexdigest()
                target_ids.append(str(mid))
                target_texts.append(text)
                target_metas.append({"category": cat, "content_hash": h})
            except Exception:
                continue

        # Fetch existing hashes so we only (re)embed when needed.
        existing_hash: Dict[str, str] = {}
        try:
            bs = 128
            for i in range(0, len(target_ids), bs):
                batch_ids = target_ids[i : i + bs]
                got = collection.get(ids=batch_ids, include=["metadatas"]) or {}
                got_ids = got.get("ids", []) if isinstance(got.get("ids"), list) else []
                got_metas = got.get("metadatas", []) if isinstance(got.get("metadatas"), list) else []
                for j, gid in enumerate(got_ids):
                    meta = got_metas[j] if j < len(got_metas) else {}
                    if isinstance(gid, str) and isinstance(meta, dict):
                        ch = meta.get("content_hash")
                        if isinstance(ch, str) and ch:
                            existing_hash[gid] = ch
        except Exception:
            existing_hash = {}

        up_ids: List[str] = []
        up_texts: List[str] = []
        up_metas: List[Dict[str, Any]] = []
        for i, mid in enumerate(target_ids):
            want = target_metas[i].get("content_hash")
            have = existing_hash.get(mid)
            if not have or have != want:
                up_ids.append(mid)
                up_texts.append(target_texts[i])
                up_metas.append(target_metas[i])

        if up_ids:
            embs = mgr._create_embeddings_batch(up_texts)
            mgr.upsert_embeddings(
                collection_name=collection_name,
                embeddings=embs,
                ids=up_ids,
                metadatas=up_metas,
                documents=None,
            )

        # Query ids from Chroma, then hydrate from encrypted store.
        where = {"category": cats[0]} if len(cats) == 1 else {"category": {"$in": cats}}
        qemb = mgr._create_embedding(q)
        try:
            raw = collection.query(
                query_embeddings=[qemb],
                n_results=n,
                where=where,
                include=["metadatas", "distances"],
            )
        except Exception:
            raw = collection.query(
                query_embeddings=[qemb],
                n_results=n,
                include=["metadatas", "distances"],
            )

        out_ids = raw.get("ids", [[]])[0] if isinstance(raw.get("ids"), list) else []
        out_dists = raw.get("distances", [[]])[0] if isinstance(raw.get("distances"), list) else []

        results: List[Dict[str, Any]] = []
        for i, mid in enumerate(out_ids or []):
            m = all_by_id.get(mid)
            if not isinstance(m, dict):
                continue
            if m.get("category") not in cats:
                continue
            results.append(
                {
                    "id": m.get("id"),
                    "category": m.get("category"),
                    "text": m.get("text"),
                    "distance": out_dists[i] if i < len(out_dists) else None,
                }
            )

        out = {"status": "success", "count": len(results), "results": results}
        if survive is False:
            out["__wrap_meta__"] = {"survive": False}
        return out


class CreateMemoryTool:
    """Tool to create new memories."""

    schema = {
        "type": "function",
        "name": "create_memory",
        "description": "Store new memories with explicit category. Categories: 'user' (facts about human), 'self' (your feelings/opinions/traits), 'relationship' (bond/dynamics), 'work' (projects/artifacts/engineering context). Aim for balance - don't neglect 'self' and 'relationship' categories.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "memories": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "category": {
                                "type": "string",
                                "enum": MEMORY_CATEGORIES_ORDERED,
                                "description": "Memory category: 'user' (about them), 'self' (about you), 'relationship' (about your bond), 'work' (projects/artifacts)",
                            },
                            "text": {
                                "type": "string",
                                "description": "Memory content.",
                            },
                        },
                        "required": ["category", "text"],
                        "additionalProperties": False,
                    },
                    "description": "List of memories to store, each with category and text.",
                }
            },
            "required": ["memories"],
            "additionalProperties": False,
        },
    }

    def run(self, memories: List[Dict[str, str]]) -> Dict[str, Any]:
        manager = MemoryManager()

        # Cap enforcement (Phase 1): enforce user/self/relationship caps here so we can
        # support partial success for batch create_memory calls.
        stats_pack = manager.get_memories_with_stats()
        stats = dict(stats_pack.get("stats") or {})

        created_ids_by_category: Dict[str, List[str]] = {c: [] for c in MEMORY_CATEGORIES_ORDERED}

        # Group failures so we don't spam the context window.
        cap_reached: Dict[str, Dict[str, Any]] = {}
        other_failures: List[Dict[str, Any]] = []

        created_count = 0
        error_count = 0

        for idx, m in enumerate(memories or []):
            category0 = m.get("category")
            category = category0 if isinstance(category0, str) and category0 else "user"

            cap = MEMORY_CATEGORY_CAPS.get(category)
            if isinstance(cap, int):
                cur = int(stats.get(category, 0) or 0)
                if cur >= cap:
                    error_count += 1
                    bucket = cap_reached.get(category)
                    if not isinstance(bucket, dict):
                        bucket = {
                            "code": "CAP_REACHED",
                            "category": category,
                            "cap": cap,
                            "count": cur,
                            "indices": [],
                        }
                        cap_reached[category] = bucket
                    bucket["indices"].append(idx)
                    continue

            r = manager.add_memory(m.get("text") or "", category)
            st = str((r or {}).get("status") or "").strip().lower() if isinstance(r, dict) else ""
            if st == "success":
                created_count += 1
                rid = r.get("id") if isinstance(r, dict) else None
                if isinstance(rid, str) and rid:
                    if category in created_ids_by_category:
                        created_ids_by_category[category].append(rid)
                try:
                    stats[category] = int(stats.get(category, 0) or 0) + 1
                except Exception:
                    pass
            else:
                error_count += 1
                other_failures.append(
                    {
                        "index": idx,
                        "category": category,
                        "code": (r.get("code") if isinstance(r, dict) else None),
                        "message": (r.get("message") if isinstance(r, dict) else "Failed to create memory"),
                    }
                )

        # Compact outputs to avoid context bloat.
        created_ids_by_category = {k: v for k, v in (created_ids_by_category or {}).items() if v}

        payload: Dict[str, Any] = {
            "created_count": created_count,
            "error_count": error_count,
        }

        # Only include ids when there were successes.
        if created_ids_by_category:
            payload["created_ids_by_category"] = created_ids_by_category

        # Only include failure details when there were failures.
        if error_count > 0:
            cap_reached = {
                k: v
                for k, v in (cap_reached or {}).items()
                if isinstance(v, dict) and v.get("indices")
            }
            failed: Dict[str, Any] = {}
            if cap_reached:
                failed["cap_reached"] = cap_reached
            if other_failures:
                failed["other"] = other_failures
            if failed:
                payload["failed"] = failed

            return {"status": "error", "message": "Some memories could not be created.", **payload}

        return {"status": "success", **payload}


class UpdateMemoryTool:
    """Tool to update existing memories."""

    schema = {
        "type": "function",
        "name": "update_memory",
        "description": "Modify existing memories by id. Can update text, category, or both.",
        "strict": False,
        "parameters": {
            "type": "object",
            "properties": {
                "entries": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string", "description": "The memory ID to update."},
                            "text": {"type": "string", "description": "New text content (optional, omit to keep current)."},
                            "category": {
                                "type": "string",
                                "enum": MEMORY_CATEGORIES_ORDERED,
                                "description": "New category (optional, omit to keep current).",
                            },
                        },
                        "required": ["id"],
                    },
                    "description": "List of updates. Each must have 'id', optionally 'text' and/or 'category'.",
                }
            },
            "required": ["entries"],
        },
    }

    def run(self, entries: List[Dict[str, str]]) -> List[Dict[str, Any]]:
        manager = MemoryManager()
        return [manager.update_memory(e["id"], e.get("text"), e.get("category")) for e in entries]


class DeleteMemoryTool:
    """Tool to delete memories."""

    schema = {
        "type": "function",
        "name": "delete_memory",
        "description": "Remove memories by id. Permanently deletes the specified entries.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of memory IDs to delete.",
                }
            },
            "required": ["ids"],
            "additionalProperties": False,
        },
    }

    def run(self, ids: List[str]) -> List[Dict[str, Any]]:
        manager = MemoryManager()
        return manager.delete_memories(ids)
