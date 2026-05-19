"""RAG (Retrieval-Augmented Generation) tools.

These tools are intentionally minimal (per product direction):
- rag_list_collections: discover available collections (names/descriptions/tags)
- rag_search: retrieve relevant chunks from a specific collection

Implementation note:
- Tools read the shared VectorDBManager from src.runtime_context (module-level runtime singleton).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ...appcore.runtime_context import Runtime


class RagListCollectionsTool:
    """Tool to list available RAG collections."""

    schema = {
        "type": "function",
        "name": "rag_list_collections",
        "description": (
            "List available document collections. "
            "Returns collection names plus short metadata such as description, tags, and counts so you can choose the right source before searching."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "survive": {
                    "anyOf": [{"type": "boolean"}, {"type": "null"}],
                    "description": "If false, this call/output will be excluded from future agent context (UI/persistence unchanged). Null/omitted defaults to true.",
                }
            },
            "required": ["survive"],
            "additionalProperties": False,
        },
    }


    def run(self, **kwargs) -> Dict[str, Any]:
        survive = kwargs.get("survive") if isinstance(kwargs, dict) else None
        """Return a safe snapshot of collection names + short metadata.

        Safety / ergonomics:
        - Never returns file paths / source file lists (avoid leaking local paths into model output).
        - Returns explicit error when VectorDB is not available instead of silently pretending "0 collections".
        """
        mgr = Runtime.get_vectordb_manager()
        if not mgr:
            ctx = Runtime.get_context()
            msg = ctx.vectordb_init_error or "VectorDB not initialized"
            return {"status": "error", "message": msg}

        try:
            raw = mgr.get_collections()
            if not isinstance(raw, list):
                return {"status": "error", "message": "Unexpected VectorDBManager.get_collections() return type"}

            collections: List[Dict[str, Any]] = []
            for col in raw:
                if not isinstance(col, dict):
                    continue

                tags = col.get("tags", [])
                if not isinstance(tags, list):
                    tags = []

                collections.append(
                    {
                        "name": col.get("name", ""),
                        "description": col.get("description", ""),
                        "tags": tags,
                        # Mildly useful, low-risk extras:
                        "count": col.get("count", 0),
                        "source_type": col.get("source_type", "unknown"),
                        "created_at": col.get("created_at"),
                        "updated_at": col.get("updated_at"),
                    }
                )

            collections.sort(key=lambda c: str(c.get("name", "")))

            out = {
                "status": "success",
                "count": len(collections),
                "collections": collections,
            }
            if survive is False:
                out["__wrap_meta__"] = {"survive": False}
            return out

        except Exception as e:
            return {"status": "error", "message": str(e)}


class RagSearchTool:
    """Tool to search a single collection and return relevant chunks."""

    schema = {
        "type": "function",
        "name": "rag_search",
        "description": (
            "Search a specific document collection and return relevant text chunks from it. "
            "Use rag_list_collections first if you do not yet know which collection to search."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "collection_name": {
                    "type": "string",
                    "description": "Target collection name to search.",
                },
                "query": {
                    "type": "string",
                    "description": "Natural-language search query.",
                },
                "n_results": {
                    "type": "integer",
                    "description": "Maximum number of chunks to return. Defaults to 5.",
                    "minimum": 1,
                    "maximum": 50,
                },
                "survive": {
                    "anyOf": [{"type": "boolean"}, {"type": "null"}],
                    "description": "If false, this call/output will be excluded from future agent context (UI/persistence unchanged). Null/omitted defaults to true.",
                },
            },
            "required": ["collection_name", "query", "n_results", "survive"],
            "additionalProperties": False,
        },
    }


    def run(
        self,
        collection_name: str,
        query: str,
        n_results: Optional[int] = None,
        survive: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Search a collection and return relevant chunks.

        Safety / ergonomics:
        - Requires the runtime-context VectorDBManager to be initialized.
        - Avoids returning local file paths in metadata.
        """
        mgr = Runtime.get_vectordb_manager()
        if not mgr:
            ctx = Runtime.get_context()
            msg = ctx.vectordb_init_error or "VectorDB not initialized"
            return {"status": "error", "message": msg}

        collection_name = (collection_name or "").strip()
        query = (query or "").strip()
        if not collection_name:
            return {"status": "error", "message": "collection_name is required"}
        if not query:
            return {"status": "error", "message": "query is required"}

        # Default + clamp.
        try:
            n = 5 if n_results is None else int(n_results)
        except Exception:
            n = 5
        n = max(1, min(50, n))

        # Fail fast with a clearer message than a deep embedding error.
        if not getattr(mgr, "openai_client", None):
            return {
                "status": "error",
                "message": "OpenAI client not configured. Please set API key in Settings.",
            }

        try:
            raw = mgr.query_collection(
                collection_name=collection_name,
                query_text=query,
                n_results=n,
            )
        except Exception as e:
            return {"status": "error", "message": str(e)}

        if not isinstance(raw, dict):
            return {"status": "error", "message": "Unexpected query result type"}
        if raw.get("status") != "success":
            # Preserve manager's error message (e.g. missing collection)
            return raw

        results = raw.get("results", {}) if isinstance(raw.get("results", {}), dict) else {}
        ids = results.get("ids", []) if isinstance(results.get("ids", []), list) else []
        docs = results.get("documents", []) if isinstance(results.get("documents", []), list) else []
        metas = results.get("metadatas", []) if isinstance(results.get("metadatas", []), list) else []
        dists = results.get("distances", []) if isinstance(results.get("distances", []), list) else []

        chunks: List[Dict[str, Any]] = []
        for i in range(min(len(ids), len(docs))):
            meta = metas[i] if i < len(metas) else {}
            if isinstance(meta, dict):
                # Strip obvious path-y stuff. (If you later want paths, we can add a flag.)
                meta = {k: v for k, v in meta.items() if "path" not in str(k).lower()}
            else:
                meta = {}

            chunks.append(
                {
                    "id": ids[i],
                    "text": docs[i],
                    "metadata": meta,
                    "distance": dists[i] if i < len(dists) else None,
                }
            )

        out = {
            "status": "success",
            "collection": collection_name,
            "query": query,
            "count": len(chunks),
            "chunks": chunks,
        }
        if survive is False:
            out["__wrap_meta__"] = {"survive": False}
        return out
