"""App-wide runtime context (singleton-ish).

appcore is the substrate: shared managers + infrastructure without threading
dependencies through every constructor.

Design goals:
- No GUI-framework dependencies.
- Thread-safe.
- One module-level singleton with a small, explicit API.

Owned here (baby-step version):
- EventBus
- ConfigManager
- PermissionsManager (placeholder)
- FsRevisionStore (shared)
- VectorDBManager
"""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import RLock
from typing import Optional

from .event_bus import EventBus
from .config_manager import ConfigManager
from .permissions import PermissionsManager
from .paths import PathsManager

from ..storage.vectordb import VectorDBManager
from ..storage.fs_revisions import FsRevisionStore
from ..storage.secure import get_fernet


# App icon (emoji for now)
APP_ICON_EMOJI = "🦊"
APP_ICON_PATH = None

_lock = RLock()


@dataclass
class _RuntimeContext:
    # Core substrate
    bus: EventBus = field(default_factory=EventBus)
    config: ConfigManager = field(default_factory=ConfigManager)
    permissions: PermissionsManager = field(default_factory=PermissionsManager)
    paths: PathsManager = field(default_factory=PathsManager)

    # Infrastructure
    fs_revision_store: Optional[FsRevisionStore] = None
    fs_revision_init_error: Optional[str] = None

    # Services
    vectordb_manager: Optional[VectorDBManager] = None
    vectordb_init_error: Optional[str] = None

    # Memory Vector Index (separate Chroma DB to keep RAG isolated)
    memory_vectordb_manager: Optional[VectorDBManager] = None
    memory_vectordb_init_error: Optional[str] = None


_ctx: _RuntimeContext = _RuntimeContext()


class Runtime:
    """Singleton-ish runtime API (class methods)."""

    # --- basics ---

    @classmethod
    def get_app_icon_emoji(cls) -> str:
        return APP_ICON_EMOJI

    @classmethod
    def get_paths(cls) -> PathsManager:
        return _ctx.paths

    @classmethod
    def get_context(cls) -> _RuntimeContext:
        return _ctx

    @classmethod
    def get_event_bus(cls) -> EventBus:
        return _ctx.bus

    @classmethod
    def get_config_manager(cls) -> ConfigManager:
        return _ctx.config

    @classmethod
    def get_permissions(cls) -> PermissionsManager:
        return _ctx.permissions

    # --- fs revisions ---

    @classmethod
    def init_fs_revision_store(cls) -> Optional[FsRevisionStore]:
        """Initialize the shared FsRevisionStore if encryption is available."""
        with _lock:
            if _ctx.fs_revision_store is not None:
                return _ctx.fs_revision_store

            try:
                if not get_fernet():
                    _ctx.fs_revision_store = None
                    _ctx.fs_revision_init_error = "Encryption unavailable"
                    return None
                _ctx.fs_revision_store = FsRevisionStore()
                _ctx.fs_revision_init_error = None
                return _ctx.fs_revision_store
            except Exception as e:
                _ctx.fs_revision_store = None
                _ctx.fs_revision_init_error = str(e)
                return None

    @classmethod
    def get_fs_revision_store(cls) -> Optional[FsRevisionStore]:
        return _ctx.fs_revision_store

    # --- vectordb (RAG) ---

    @classmethod
    def get_vectordb_manager(cls) -> Optional[VectorDBManager]:
        return _ctx.vectordb_manager

    @classmethod
    def init_vectordb_manager(
        cls,
        *,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        embedding_model: str = "text-embedding-3-small",
    ) -> Optional[VectorDBManager]:
        """Initialize (or refresh) VectorDBManager and store it in the global context."""
        with _lock:
            if _ctx.vectordb_manager is None:
                try:
                    _ctx.vectordb_manager = VectorDBManager(
                        api_key=api_key,
                        base_url=base_url,
                        embedding_model=embedding_model,
                    )
                    _ctx.vectordb_init_error = None
                except Exception as e:
                    _ctx.vectordb_manager = None
                    _ctx.vectordb_init_error = str(e)
            else:
                try:
                    _ctx.vectordb_manager.update_credentials(api_key=api_key, base_url=base_url)
                    _ctx.vectordb_manager.embedding_model = embedding_model
                    _ctx.vectordb_init_error = None
                except Exception as e:
                    _ctx.vectordb_init_error = str(e)

            if _ctx.vectordb_manager is not None:
                _ctx.bus.publish(
                    "vectordb.ready",
                    {"embedding_model": embedding_model, "base_url": base_url},
                )
            else:
                _ctx.bus.publish(
                    "vectordb.error",
                    {"error": _ctx.vectordb_init_error or "init failed"},
                )

            return _ctx.vectordb_manager

    @classmethod
    def update_vectordb_credentials(
        cls,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        embedding_model: Optional[str] = None,
    ) -> None:
        with _lock:
            mgr = _ctx.vectordb_manager
            if mgr:
                mgr.update_credentials(api_key=api_key, base_url=base_url)
                if embedding_model is not None:
                    mgr.embedding_model = embedding_model

                _ctx.bus.publish(
                    "vectordb.credentials.updated",
                    {"base_url": base_url, "embedding_model": embedding_model},
                )

            mmgr = _ctx.memory_vectordb_manager
            if mmgr:
                mmgr.update_credentials(api_key=api_key, base_url=base_url)
                if embedding_model is not None:
                    mmgr.embedding_model = embedding_model

    # --- vectordb (memory index; separate DB dir) ---

    @classmethod
    def get_memory_vectordb_manager(cls) -> Optional[VectorDBManager]:
        return _ctx.memory_vectordb_manager

    @classmethod
    def init_memory_vectordb_manager(
        cls,
        *,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        embedding_model: str = "text-embedding-3-small",
    ) -> Optional[VectorDBManager]:
        """Initialize (or refresh) VectorDBManager for memory indexing.

        Uses a separate Chroma persistent directory so RAG collections never see memory collections.
        """
        with _lock:
            if _ctx.memory_vectordb_manager is None:
                try:
                    _ctx.memory_vectordb_manager = VectorDBManager(
                        api_key=api_key,
                        base_url=base_url,
                        embedding_model=embedding_model,
                        db_dir_name="ai-agent-desktop-chromadb-memories",
                        allow_reset=False,
                    )
                    _ctx.memory_vectordb_init_error = None
                except Exception as e:
                    _ctx.memory_vectordb_manager = None
                    _ctx.memory_vectordb_init_error = str(e)
            else:
                try:
                    _ctx.memory_vectordb_manager.update_credentials(api_key=api_key, base_url=base_url)
                    _ctx.memory_vectordb_manager.embedding_model = embedding_model
                    _ctx.memory_vectordb_init_error = None
                except Exception as e:
                    _ctx.memory_vectordb_init_error = str(e)

            return _ctx.memory_vectordb_manager
