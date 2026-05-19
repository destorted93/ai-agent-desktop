"""Main application entry point for AI Agent Desktop."""

import sys
import os
import threading
from datetime import datetime, timezone
from typing import Optional, List, Generator, Dict, Any

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QObject, pyqtSignal, QTimer
from .core import Agent
from .app_services.agent_factory import create_agent, get_api_mode_from_app
from .storage import SessionsManager, SecureStorage, MemoryManager
from .storage.sessions_manager import SessionManager
from .storage.vectordb import VectorDBManager
from .appcore.runtime_context import Runtime
from .tools import get_default_tools
from .services import TranscribeService
from .canvas import CanvasManager



class Application(QObject):
    """Main application class (spine/facade).

    Bus handlers live in `src/app_handlers/*` and are wired in `_register_bus_handlers()`.
    This class owns process-wide runtime state and exposes a small API surface.
    """
    
    def __init__(self):
        super().__init__()
        # ConfigManager now lives in appcore Runtime (singletons, not a jungle).
        self.config = Runtime.get_config_manager()
        try:
            self.config.load()
        except Exception:
            pass
        self.sessions_manager = SessionsManager()
        # Keep inner-voice store separate (single persistent file).
        self._inner_voice_session_manager = SessionManager(store_id="session_inner_voice")

        # Inference state (used to block session switching/creation).
        # Note: nested sub-agent runs are allowed; we track depth so child runs don't
        # accidentally flip the parent to "not running".
        self._inference_lock = threading.Lock()
        self._inference_depth = 0
        # Long-term memory (default to primary agent's store; finalized in initialize()).
        self.memory_manager = MemoryManager(agent_id="aria")
        self._bus_unsubs: List[Any] = []
        self.secure_storage = SecureStorage()
        # Fs revision store now lives in appcore Runtime (shared).
        try:
            Runtime.init_fs_revision_store()
        except Exception:
            pass
        self.fs_revision_store = Runtime.get_fs_revision_store()
        self.vectordb_manager: Optional[VectorDBManager] = None
        self.agent: Optional[Agent] = None
        self.transcribe_service: Optional[TranscribeService] = None
        self.canvas_manager: Optional[CanvasManager] = None
        self.widget = None  # Will be set after UI import
        self.qt_app: Optional[QApplication] = None
        self._bus_pump_timer: Optional[QTimer] = None
        self._stop_requested = False


        # Active group-session participant agent (for Stop propagation).
        # Group sessions run participants sequentially, so at most one is active at a time.
        self._active_group_agent: Optional[Agent] = None
        # Active sub-agent run registry (for stop propagation).
        # parent_run_id -> {subagent_id: Agent}
        self._active_subagents_lock = threading.Lock()
        self._active_subagents_by_parent_run_id: Dict[str, Dict[str, Any]] = {}

    def initialize(self):
        """Initialize the application."""
        # Get API key from secure storage (keyring)
        api_key = self.secure_storage.get_secret("api_token") or None
        
        # Reload configuration (app-data ConfigRoot)
        try:
            self.config.load()
        except Exception:
            pass

        base_url = str(self.config.app.api.base_url or "").strip()

        # Permissions policy (V1: store in Runtime; no behavior change yet).
        try:
            Runtime.get_permissions().set_from_config(
                filesystem_permission_required=bool(self.config.app.tools.filesystem_permission_required),
                terminal_permission_required=bool(self.config.app.tools.terminal_permission_required),
            )
        except Exception:
            pass

        # Project root (resolved by tools via Runtime.get_paths(); keep here for other app uses).
        project_root = str(self.config.app.paths.project_root or "").strip() or os.getcwd()

        # Lock Runtime paths to the configured project root (V1).
        try:
            Runtime.get_paths().set_project_root(project_root)
        except Exception:
            pass

        # Primary agent (must exist in Config/agents)
        aria_spec = self.config.get_primary_agent()
        if aria_spec is None:
            raise RuntimeError("Primary agent not found in Config/agents (expected id 'aria')")

        aria_name = str(aria_spec.display_name)
        # Boot-time fallback only; per-run we override with a session-scoped prompt cache key.
        aria_user_id = f"default_user:{aria_spec.id}"
        agent_config = self.config.build_runtime_config(
            aria_spec,
            allow_memory=True,
            allow_session_meta=True,
            allow_recursion=True,
        )

        # Ensure app-level memory bus endpoints operate on the primary agent's memory store.
        try:
            self.memory_manager = MemoryManager(agent_id=str(aria_spec.id))
        except Exception:
            pass

        all_tools = get_default_tools()
        tools = self.config.filter_tools(
            all_tools,
            aria_spec,
            allow_memory=True,
            allow_session_meta=True,
            allow_recursion=True,
        )

        # Agent runner mode (compat switch; default remains Responses API).
        api_mode = get_api_mode_from_app(self)

        self.agent = create_agent(
            api_key=api_key,
            base_url=base_url,
            name=aria_name,
            tools=tools,
            user_id=aria_user_id,
            config=agent_config,
            agent_id=str(aria_spec.id),
            api_mode=api_mode,
        )
        
        # Create transcribe service (shares OpenAI client if available)
        if api_key:
            try:
                from openai import OpenAI
                client = OpenAI(api_key=api_key, base_url=base_url) if base_url else OpenAI(api_key=api_key)
                self.transcribe_service = TranscribeService(client=client)
            except Exception:
                self.transcribe_service = None
        else:
            self.transcribe_service = None
        
        # Initialize VectorDB manager (for document collections/RAG) via runtime context
        mgr = Runtime.init_vectordb_manager(
            api_key=api_key,
            base_url=base_url,
            embedding_model=self.config.app.embedding.model,
        )
        if mgr is None:
            err = Runtime.get_context().vectordb_init_error
            print(f"[APP] Failed to initialize VectorDB manager: {err or 'unknown error'}")
        self.vectordb_manager = mgr

        # Canvas manager (Canvas Studio backend) — stored in app-data Sandbox.
        try:
            self.canvas_manager = CanvasManager(default_injected_max_side=1024)
        except Exception:
            self.canvas_manager = None

        # Register event-bus command handlers (UI -> app orchestration)
        self._register_bus_handlers()
    
    def _register_bus_handlers(self) -> None:
        """Subscribe app-level handlers to the in-process event bus.

        This is the bridge that lets the GUI talk to the app without direct method calls.
        """
        # Avoid double-registering if initialize() is called more than once.
        if getattr(self, "_bus_unsubs", None):
            if len(self._bus_unsubs) > 0:
                return

        bus = Runtime.get_event_bus()

        # === Documents / VectorDB ===
        from .app_handlers.bus_documents import register_documents_handlers

        self._bus_unsubs.extend(register_documents_handlers(self, bus))

        # === Settings ===
        from .app_handlers.bus_settings import register_settings_handlers

        self._bus_unsubs.extend(register_settings_handlers(self, bus))

        # === Sessions (multi-session) ===
        from .app_handlers.bus_sessions import register_sessions_handlers

        self._bus_unsubs.extend(register_sessions_handlers(self, bus))

        # === Sub-agent definitions (ConfigRoot/agents) ===
        from .app_handlers.bus_agents import register_agents_handlers

        self._bus_unsubs.extend(register_agents_handlers(self, bus))

        # === Session meta + stats ===
        from .app_handlers.bus_session_meta import register_session_meta_handlers
        from .app_handlers.bus_session_stats import register_session_stats_handlers

        self._bus_unsubs.extend(register_session_meta_handlers(self, bus))
        self._bus_unsubs.extend(register_session_stats_handlers(self, bus))

        # Fs revision diffs (computed on demand)
        from .app_handlers.bus_fs_diffs import register_fs_diffs_handlers

        self._bus_unsubs.extend(register_fs_diffs_handlers(self, bus))


        # === Agent + Sub-agent runtime ===
        from .app_handlers.bus_agent_runtime import register_agent_runtime_handlers

        self._bus_unsubs.extend(register_agent_runtime_handlers(self, bus))

        # === Transcribe ===
        from .app_handlers.bus_transcribe import register_transcribe_handlers

        self._bus_unsubs.extend(register_transcribe_handlers(self, bus))

        # === Canvas Studio (persistent canvases in Sandbox) ===
        from .app_handlers.bus_canvas import register_canvas_handlers

        self._bus_unsubs.extend(register_canvas_handlers(self, bus))


        # === Memories (primary + multi-store) ===
        from .app_handlers.bus_memories import register_memories_handlers

        self._bus_unsubs.extend(register_memories_handlers(self, bus))

        # === Inner Voice (Ariane) ===
        from .app_handlers.bus_inner_voice import register_inner_voice_handlers

        self._bus_unsubs.extend(register_inner_voice_handlers(self, bus))

        # === App lifecycle (restart) ===
        from .app_handlers.bus_app_lifecycle import register_app_lifecycle_handlers

        self._bus_unsubs.extend(register_app_lifecycle_handlers(self, bus))

    def _bus_reply(self, reply_topic: str, payload: Dict[str, Any]) -> None:
        Runtime.get_event_bus().publish(reply_topic, payload)


    def update_api_key(self, api_key: str, base_url: Optional[str] = None):
        """Update API key and reinitialize agent."""
        if self.agent:
            self.agent.update_api_key(api_key, base_url)

        # Update VectorDB manager credentials (runtime context)
        Runtime.update_vectordb_credentials(
            api_key=api_key,
            base_url=base_url,
            embedding_model=self.config.app.embedding.model,
        )
        self.vectordb_manager = Runtime.get_vectordb_manager()

        # Reinitialize transcribe service
        if api_key:
            try:
                from openai import OpenAI

                client = OpenAI(api_key=api_key, base_url=base_url) if base_url else OpenAI(api_key=api_key)
                self.transcribe_service = TranscribeService(client=client)
            except Exception:
                pass


    # -----------------------------------------------------------------
    # Orchestration helpers
    # -----------------------------------------------------------------
    # EventBus subscriptions live in src/app_handlers/* and are wired by
    # `Application._register_bus_handlers()`.

    def _is_inference_running(self) -> bool:
        try:
            with self._inference_lock:
                return int(self._inference_depth or 0) > 0
        except Exception:
            return False

    def _set_inference_running(self, running: bool) -> None:
        """Increment/decrement inference depth.

        This allows nested sub-agent runs without corrupting "is running" state.
        """
        try:
            with self._inference_lock:
                d = int(self._inference_depth or 0)
                if bool(running):
                    d += 1
                else:
                    d = max(0, d - 1)
                self._inference_depth = d
        except Exception:
            pass

    def _register_active_subagent(self, parent_run_id: Optional[str], subagent_id: str, agent: Any) -> None:
        try:
            pr = str(parent_run_id).strip() if isinstance(parent_run_id, str) else ""
            sid = str(subagent_id).strip() if isinstance(subagent_id, str) else ""
            if not pr or not sid:
                return
            with self._active_subagents_lock:
                self._active_subagents_by_parent_run_id.setdefault(pr, {})[sid] = agent
        except Exception:
            pass

    def _unregister_active_subagent(self, parent_run_id: Optional[str], subagent_id: str) -> None:
        try:
            pr = str(parent_run_id).strip() if isinstance(parent_run_id, str) else ""
            sid = str(subagent_id).strip() if isinstance(subagent_id, str) else ""
            if not pr or not sid:
                return
            with self._active_subagents_lock:
                d = self._active_subagents_by_parent_run_id.get(pr)
                if isinstance(d, dict):
                    try:
                        d.pop(sid, None)
                    except Exception:
                        pass
                    if not d:
                        try:
                            self._active_subagents_by_parent_run_id.pop(pr, None)
                        except Exception:
                            pass
        except Exception:
            pass


    # -----------------------------------------------------------------
    # Stop propagation (main run -> sub-agent runs)
    # -----------------------------------------------------------------

    def _stop_active_subagents(self, parent_run_id: Optional[str]) -> None:
        """Best-effort: stop all sub-agent Agent instances spawned under a parent run."""
        try:
            pr = str(parent_run_id).strip() if isinstance(parent_run_id, str) else ""
            with self._active_subagents_lock:
                if pr:
                    agents = list((self._active_subagents_by_parent_run_id.get(pr) or {}).values())
                else:
                    agents = []
                    for dd in (self._active_subagents_by_parent_run_id or {}).values():
                        if isinstance(dd, dict):
                            agents.extend(list(dd.values()))

            for a in agents:
                try:
                    if a is not None and hasattr(a, "stop"):
                        a.stop()
                except Exception:
                    pass
        except Exception:
            pass


    # -----------------------------------------------------------------
    # Misc helpers
    # -----------------------------------------------------------------

    def _stamp_user_message(self, message: str) -> str:
        """Prefix the user message with a timestamp so the agent can perceive time.

        Timestamp is embedded into the message text (per request), clearly marked
        as app-generated metadata.
        """
        ts = (
            datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
        return f"META(received_at_utc={ts})\n{message}"

    def stop_agent(self):
        """Request agent to stop (main run + any active group participant)."""
        self._stop_requested = True
        if self.agent:
            self.agent.stop()
        # If a group session is currently running a participant, stop it too.
        try:
            if getattr(self, "_active_group_agent", None) is not None:
                self._active_group_agent.stop()
        except Exception:
            pass
    
    def run_agent(
        self,
        message: Optional[str] = None,
        files: Optional[List[str]] = None,
        images: Optional[List[str]] = None,
        *,
        session_id: str,
        run_id: Optional[str] = None,
    ) -> Generator[Dict[str, Any], None, None]:
        """Single-agent runner.

        Delegates to src/app_handlers/agent_run.py (extracted behavior-preserving).
        """
        from .app_handlers.agent_run import run_agent as _run_agent

        yield from _run_agent(
            self,
            message=message,
            files=files,
            images=images,
            session_id=session_id,
            run_id=run_id,
        )
    
    def get_session_messages(self, session_id: str) -> List[Dict]:
        """Get session messages (unwrapped, for API use)."""
        return self.sessions_manager.get_messages(session_id=session_id)


    def run_group_session(
        self,
        message: Optional[str] = None,
        files: Optional[List[str]] = None,
        images: Optional[List[str]] = None,
        *,
        session_id: str,
        run_id: Optional[str] = None,
    ) -> Generator[Dict[str, Any], None, None]:
        """Group Session runner.

        Delegates to src/app_handlers/group_session.py (extracted verbatim).
        """
        from .app_handlers.group_session import run_group_session as _run_group_session

        yield from _run_group_session(
            self,
            message=message,
            files=files,
            images=images,
            session_id=session_id,
            run_id=run_id,
        )

    def get_session_entries_wrapped(self, session_id: str) -> List[Dict]:
        """Get wrapped session entries with IDs and metadata (for UI display)."""
        return self.sessions_manager.get_entries_wrapped(session_id=session_id)

    # -----------------------------------------------------------------
    # Session-derived stats (token usage)
    # -----------------------------------------------------------------

    def _update_session_token_stats_meta(self, session_id: str) -> None:
        """Recompute token stats and store them in sessions index meta (cache)."""
        from .app_handlers.bus_session_stats import update_session_token_stats_meta

        return update_session_token_stats_meta(self, session_id=session_id)
    
    def delete_entries_from_id(
        self,
        entry_id: str,
        session_id: str,
        undo_file_edits: bool = False,
        origin_action: str = "",
    ) -> Dict[str, Any]:
        """Delete a message and all subsequent messages.

        Optional: if undo_file_edits=True, attempt to undo all filesystem transactions
        in the deleted tail (newest->oldest) before deleting entries.

        NOTE: deletion still proceeds even if some undos fail.
        On failure, we append a system_notice error entry (not sent to the agent).
        """
        # Delegate to SessionsManager (storage-owned). App decides *when* to call it.
        proj_root = None
        sb_root = None
        try:
            from .appcore.runtime_context import Runtime

            paths = Runtime.get_paths()
            cfg_proj = None
            try:
                cfg_proj = getattr(getattr(getattr(self, "config", None), "app", None), "paths", None)
                cfg_proj = getattr(cfg_proj, "project_root", None)
            except Exception:
                cfg_proj = None

            proj_root = paths.get_project_root(config_project_root=(str(cfg_proj) if isinstance(cfg_proj, str) else None))
            sb_root = paths.get_sandbox_root(ensure_exists=True)
        except Exception:
            try:
                proj_root = os.getcwd()
            except Exception:
                proj_root = None
            try:
                from .storage.sandbox_storage import get_sandbox_root

                sb_root = str(get_sandbox_root(ensure_exists=True))
            except Exception:
                sb_root = None

        return self.sessions_manager.delete_entries_from_id(
            session_id=str(session_id),
            entry_id=str(entry_id),
            undo_file_edits=bool(undo_file_edits),
            origin_action=str(origin_action or ""),
            fs_revision_store=getattr(self, "fs_revision_store", None),
            project_root=proj_root,
            sandbox_root=sb_root,
        )

    def delete_session(self, session_id: str) -> Dict[str, Any]:
        """Delete a full user session. Storage owns session-linked cleanup."""
        result = self.sessions_manager.delete_session(session_id=session_id)
        return result if isinstance(result, dict) else {"status": "error", "message": "Failed to delete session"}
    
    def clear_session(self, session_id: str) -> bool:
        """Clear the current session."""
        self.sessions_manager.clear_entries(session_id=session_id)
        return True
    
    def set_session_entries(self, entries: List[Dict], session_id: str) -> Dict[str, Any]:
        """Replace session entries with new data (e.g., from loaded file).
        
        Args:
            entries: List of wrapped session entries to save
            session_id: Chat session ID (for future multi-chat support)
            
        Returns:
            Dict with 'status' key ('success' or 'error')
        """
        try:
            self.sessions_manager.replace_entries_wrapped(session_id=session_id, entries=entries)
            return {"status": "success"}
        except Exception as e:
            return {"status": "error", "message": str(e)}
    
    # === Memory Methods ===
    
    def get_memories(self) -> List[Dict]:
        """Get all user memories."""
        # Reload from disk to get latest (tools create their own MemoryManager instances)
        self.memory_manager.load()
        return self.memory_manager.get_memories()
    
    def set_memories(self, memories: List[Dict]) -> Dict[str, Any]:
        """Replace all memories with new data.
        
        Args:
            memories: List of memory dicts to save
            
        Returns:
            Dict with 'status' key ('success' or 'error')
        """
        self.memory_manager.memories = memories
        return self.memory_manager.save()
    
    def transcribe(self, audio_data: bytes, language: str = "en") -> Optional[Dict]:
        """Transcribe audio data."""
        if self.transcribe_service:
            return self.transcribe_service.transcribe(audio_data=audio_data, language=language)
        return None

    def run(self):
        """Run the application."""
        # Windows-specific: Set AppUserModelID so taskbar shows our icon, not Python's
        if sys.platform == "win32":
            try:
                import ctypes

                # Set unique App ID so Windows doesn't group us with Python
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("AIAgent.Desktop.Application")
            except Exception:
                pass  # Not critical if it fails

        # Create Qt app
        self.qt_app = QApplication(sys.argv)
        self.qt_app.setQuitOnLastWindowClosed(False)

        # Initialize agent and services
        self.initialize()

        # Import UI here to avoid circular imports
        from .ui import FloatingWidget

        # Create widget with app reference and icon emoji (widget creates QIcon)
        icon_emoji = Runtime.get_app_icon_emoji()
        self.widget = FloatingWidget(app=self, icon_emoji=icon_emoji)

        # Show widget
        self.widget.show()

        # Pump the in-process event bus on the UI thread (framework-agnostic bus; Qt timer here).
        self._bus_pump_timer = QTimer()
        self._bus_pump_timer.setInterval(50)
        self._bus_pump_timer.timeout.connect(lambda: Runtime.get_event_bus().pump(max_events=50))
        self._bus_pump_timer.start()

        # Run event loop
        return self.qt_app.exec()

def main():
    """Main entry point."""
    app = Application()
    sys.exit(app.run())

if __name__ == "__main__":
    main()
