"""Documents/VectorDB bus handlers (extracted from src/app.py).

Move-first, refactor-later.
- Preserve bus topics.
- Preserve payload/response shapes.
- No UI imports.
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Dict, List

from ..appcore.runtime_context import Runtime


def register_documents_handlers(app: Any, bus: Any) -> List[Callable[[], None]]:
    """Register Documents/VectorDB bus handlers. Returns unsubscribe callables."""
    unsubs: List[Callable[[], None]] = []

    unsubs.append(bus.subscribe("documents.cmd.list_collections", lambda ev: bus_list_collections(app, ev)))
    unsubs.append(bus.subscribe("documents.cmd.get_chunks", lambda ev: bus_get_chunks(app, ev)))
    unsubs.append(bus.subscribe("documents.cmd.delete_collection", lambda ev: bus_delete_collection(app, ev)))
    unsubs.append(bus.subscribe("documents.cmd.create_collection_from_files", lambda ev: bus_create_collection_from_files(app, ev)))

    return unsubs


def bus_list_collections(app: Any, event) -> None:
    payload = getattr(event, "payload", {}) or {}
    reply_topic = payload.get("reply_topic")
    if not reply_topic:
        return

    def work():
        if not getattr(app, "vectordb_manager", None):
            app._bus_reply(reply_topic, {"status": "error", "message": "VectorDB not initialized"})
            return
        try:
            cols = app.vectordb_manager.get_collections()
            app._bus_reply(reply_topic, {"status": "success", "collections": cols})
        except Exception as e:
            app._bus_reply(reply_topic, {"status": "error", "message": str(e)})

    threading.Thread(target=work, daemon=True).start()


def bus_get_chunks(app: Any, event) -> None:
    payload = getattr(event, "payload", {}) or {}
    reply_topic = payload.get("reply_topic")
    collection_name = (payload.get("collection_name") or "").strip()
    if not reply_topic:
        return
    if not collection_name:
        app._bus_reply(reply_topic, {"status": "error", "message": "collection_name is required"})
        return

    def work():
        if not getattr(app, "vectordb_manager", None):
            app._bus_reply(reply_topic, {"status": "error", "message": "VectorDB not initialized"})
            return
        try:
            result = app.vectordb_manager.get_collection_documents(
                collection_name=collection_name,
                limit=payload.get("limit"),
                offset=int(payload.get("offset", 0) or 0),
                include_embeddings=False,
            )
            app._bus_reply(reply_topic, result)
        except Exception as e:
            app._bus_reply(reply_topic, {"status": "error", "message": str(e)})

    threading.Thread(target=work, daemon=True).start()


def bus_delete_collection(app: Any, event) -> None:
    payload = getattr(event, "payload", {}) or {}
    reply_topic = payload.get("reply_topic")
    name = (payload.get("name") or "").strip()
    if not reply_topic:
        return
    if not name:
        app._bus_reply(reply_topic, {"status": "error", "message": "name is required"})
        return

    def delete_collection(name: str) -> Dict[str, Any]:
        if not getattr(app, "vectordb_manager", None):
            return {"status": "error", "message": "VectorDB not initialized"}
        try:
            result = app.vectordb_manager.delete_collection(name)
            if result.get("status") == "success":
                Runtime.get_event_bus().publish(
                    "vectordb.collections.changed",
                    {"action": "deleted", "name": name},
                )
            return result
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def work():
        result = delete_collection(name)
        app._bus_reply(reply_topic, result)

    threading.Thread(target=work, daemon=True).start()


def bus_create_collection_from_files(app: Any, event) -> None:
    payload = getattr(event, "payload", {}) or {}
    reply_topic = payload.get("reply_topic")
    progress_topic = payload.get("progress_topic")

    name = (payload.get("name") or "").strip()
    description = (payload.get("description") or "").strip()
    tags = payload.get("tags") or []
    file_paths = payload.get("file_paths") or []
    chunk_size = int(payload.get("chunk_size", 1000) or 1000)
    chunk_overlap = int(payload.get("chunk_overlap", 200) or 200)

    if not reply_topic:
        return
    if not name:
        app._bus_reply(reply_topic, {"status": "error", "message": "name is required"})
        return
    if not isinstance(file_paths, list) or not file_paths:
        app._bus_reply(reply_topic, {"status": "error", "message": "file_paths must be a non-empty list"})
        return

    # Allow file_paths to contain either strings (local files / URL strings)
    # or dicts (URL descriptors with flags like include_child_pages).
    normalized_paths: List[Any] = []
    for item in file_paths:
        if isinstance(item, str):
            s = item.strip()
            if not s:
                app._bus_reply(reply_topic, {"status": "error", "message": "file_paths contains an empty string"})
                return
            normalized_paths.append(s)
        elif isinstance(item, dict):
            url = str(item.get("url") or item.get("page_url") or "").strip()
            if not url:
                app._bus_reply(reply_topic, {"status": "error", "message": "file_paths contains a URL dict missing 'url'"})
                return
            normalized_paths.append({
                "url": url,
                "include_child_pages": bool(item.get("include_child_pages", False)),
            })
        else:
            app._bus_reply(reply_topic, {"status": "error", "message": "file_paths must contain only strings or dicts"})
            return

    file_paths = normalized_paths

    def progress(msg: str) -> None:
        if progress_topic:
            Runtime.get_event_bus().publish(progress_topic, {"message": msg})

    def work():
        if not getattr(app, "vectordb_manager", None):
            app._bus_reply(reply_topic, {"status": "error", "message": "VectorDB not initialized"})
            return
        if not app.vectordb_manager.openai_client:
            app._bus_reply(
                reply_topic,
                {
                    "status": "error",
                    "message": "OpenAI client not configured. Please set API key in Settings.",
                },
            )
            return

        created = False
        try:
            progress(f"Creating collection '{name}'...")
            create_result = app.vectordb_manager.create_collection(name, description, "document", tags)
            if create_result.get("status") != "success":
                app._bus_reply(reply_topic, create_result)
                return
            created = True

            progress("Chunking documents...")
            all_chunks: List[str] = []
            all_metadatas: List[Dict[str, Any]] = []
            for fp in file_paths:
                chunk_list = app.vectordb_manager.chunk_document(fp, chunk_size, chunk_overlap)
                for chunk in chunk_list:
                    all_chunks.append(chunk.get("text", ""))
                    all_metadatas.append(chunk.get("metadata", {}))

            if not all_chunks:
                raise RuntimeError("No content extracted from documents")

            progress(f"Embedding + adding {len(all_chunks)} chunks...")
            add_result = app.vectordb_manager.add_documents(name, all_chunks, all_metadatas)
            if add_result.get("status") != "success":
                raise RuntimeError(add_result.get("message", "Failed to add documents"))

            Runtime.get_event_bus().publish(
                "vectordb.collections.changed",
                {"action": "created", "name": name, "chunks_added": len(all_chunks)},
            )

            app._bus_reply(
                reply_topic,
                {
                    "status": "success",
                    "collection": name,
                    "chunks_added": len(all_chunks),
                    "files_processed": len(file_paths),
                },
            )

        except Exception as e:
            if created:
                try:
                    app.vectordb_manager.delete_collection(name)
                    Runtime.get_event_bus().publish(
                        "vectordb.collections.changed",
                        {"action": "deleted", "name": name},
                    )
                except Exception:
                    pass

            app._bus_reply(reply_topic, {"status": "error", "message": str(e)})

    threading.Thread(target=work, daemon=True).start()
