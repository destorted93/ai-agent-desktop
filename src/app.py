"""Main application entry point for AI Agent Desktop."""

import json
import sys
import os
from typing import Optional, List, Generator, Dict, Any

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QObject, pyqtSignal

from .config import get_app_config, AgentConfig
from .core import Agent
from .storage import ChatHistoryManager, SecureStorage, MemoryManager, VectorDBManager
from .tools import get_default_tools
from .services import TranscribeService


class Application(QObject):
    """Main application class - the backbone/orchestrator."""
    
    # Signal to emit agent events to UI
    agent_event = pyqtSignal(dict)
    
    def __init__(self):
        super().__init__()
        self.app_config = get_app_config()
        self.history_manager = ChatHistoryManager()
        self.memory_manager = MemoryManager()
        self.secure_storage = SecureStorage()
        self.vectordb_manager: Optional[VectorDBManager] = None
        self.agent: Optional[Agent] = None
        self.transcribe_service: Optional[TranscribeService] = None
        self.widget = None  # Will be set after UI import
        self.qt_app: Optional[QApplication] = None
        self._stop_requested = False
    
    def initialize(self):
        """Initialize the application."""
        # Get API key from secure storage (keyring)
        api_key = self.secure_storage.get_secret("api_token") or None
        
        # Get base URL: config.yaml takes precedence, then secure_storage, then default
        base_url = self.app_config.api.base_url
        if not base_url:
            base_url = self.secure_storage.get_config_value("base_url", "")
        
        # Get project root
        project_root = self.app_config.tools.project_root or os.getcwd()
        
        # Create agent config from YAML (with fallback to agent_config.py defaults)
        agent_config = AgentConfig.from_yaml()
        
        # Get tools
        tools = get_default_tools(
            project_root=project_root,
            permission_required=self.app_config.tools.terminal_permission_required
        )
        
        # Create agent
        self.agent = Agent(
            api_key=api_key,
            base_url=base_url,
            name=self.app_config.agent_name,
            tools=tools,
            user_id=self.app_config.user_id,
            config=agent_config
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
        
        # Initialize VectorDB manager (for document collections/RAG)
        try:
            self.vectordb_manager = VectorDBManager(
                api_key=api_key,
                base_url=base_url,
                embedding_model=self.app_config.embedding.model
            )
        except Exception as e:
            print(f"[APP] Failed to initialize VectorDB manager: {e}")
            self.vectordb_manager = None
    
    def update_api_key(self, api_key: str, base_url: Optional[str] = None):
        """Update API key and reinitialize agent."""
        if self.agent:
            self.agent.update_api_key(api_key, base_url)
        
        # Update VectorDB manager credentials
        if self.vectordb_manager:
            self.vectordb_manager.update_credentials(api_key, base_url)
        
        # Reinitialize transcribe service
        if api_key:
            try:
                from openai import OpenAI
                client = OpenAI(api_key=api_key, base_url=base_url) if base_url else OpenAI(api_key=api_key)
                self.transcribe_service = TranscribeService(client=client)
            except Exception:
                pass
    
    def get_current_settings(self) -> Dict[str, str]:
        """Get current settings for display in UI.
        
        Returns:
            Dict with base_url and api_token (if available)
        """
        # Prefer config.yaml, fallback to secure_storage
        base_url = self.app_config.api.base_url or self.secure_storage.get_config_value("base_url", "https://api.openai.com/v1")
        api_token = self.secure_storage.get_secret("api_token") or ""
        
        return {
            "base_url": base_url,
            "api_token": api_token
        }
    
    def save_settings(self, settings: Dict[str, str]) -> bool:
        """Save settings and update services.
        
        Args:
            settings: Dict with 'base_url' and 'api_token' keys
            
        Returns:
            True if successful, False otherwise
        """
        try:
            base_url = settings.get("base_url", "").strip()
            api_token = settings.get("api_token", "").strip()
            
            # Save base_url to secure_storage (not config.yaml!)
            self.secure_storage.set_config_value("base_url", base_url)
            # Reload config to pick up any changes
            self.app_config = get_app_config()
            
            # Save api_token to keyring (secure storage)
            if api_token:
                self.secure_storage.set_secret("api_token", api_token)
                # Update services with new credentials
                self.update_api_key(api_token, base_url if base_url else None)
            else:
                # Clear token if empty - remove from storage and invalidate services
                self.secure_storage.delete_secret("api_token")
                # Clear agent client (empty string will set client to None)
                if self.agent:
                    self.agent.update_api_key("", base_url if base_url else None)
                # Clear transcribe service
                self.transcribe_service = None
            
            return True
            
        except Exception as e:
            print(f"[APP] Failed to save settings: {e}")
            return False
    
    def stop_agent(self):
        """Request agent to stop."""
        self._stop_requested = True
        if self.agent:
            self.agent.stop()
    
    def run_agent(
        self,
        message: Optional[str] = None,
        files: Optional[List[str]] = None,
        images: Optional[List[str]] = None,
        chat_id: str = "default"
    ) -> Generator[Dict[str, Any], None, None]:
        """Run agent and yield events. This is the main orchestration method.
        
        The app handles:
        1. Running the agent
        2. Yielding events to the caller (UI)
        3. Saving chat history when done
        4. Saving generated images when done
        """
        self._stop_requested = False
        
        if not self.agent:
            yield {
                "type": "error",
                "agent_name": "System",
                "content": {"message": "Agent not initialized. Please configure API key in Settings."}
            }
            yield {"type": "stream.finished", "agent_name": "System", "content": {}}
            return
        
        try:
            for event in self.agent.run(
                message=message,
                input_messages=self.history_manager.get_history(chat_id=chat_id),
                files=files,
                images=images,
                chat_id=chat_id
            ):
                if self._stop_requested:
                    yield {
                        "type": "response.agent.done",
                        "agent_name": "Agent",
                        "content": {"stopped": True}
                    }
                    break
                
                # Handle agent done event - save history here in the app
                if event.get("type") == "response.agent.done":
                    content = event.get("content", {})
                    saved_entry_ids = []
                    user_entry_id = None
                    
                    # Save chat history
                    chat_history = content.get("chat_history", [])
                    if chat_history:
                        try:
                            saved_entry_ids = self.history_manager.append_entries(chat_history)
                            print(f"[APP] Saved {len(chat_history)} entries to chat history")
                            
                            # Find the user message ID (first entry with role="user")
                            for i, entry in enumerate(chat_history):
                                if entry.get("role") == "user" and i < len(saved_entry_ids):
                                    user_entry_id = saved_entry_ids[i]
                                    break
                        except Exception as e:
                            print(f"[APP] Failed to save chat history: {e}")
                    
                    # Save generated images
                    generated_images = content.get("generated_images", [])
                    if generated_images:
                        try:
                            self.history_manager.add_generated_images(generated_images)
                            print(f"[APP] Saved {len(generated_images)} generated images")
                        except Exception as e:
                            print(f"[APP] Failed to save generated images: {e}")
                    
                    # Add saved IDs to the event content for UI to use
                    enriched_event = {
                        "type": event.get("type"),
                        "agent_name": event.get("agent_name"),
                        "content": {
                            **content,
                            "saved_entry_ids": saved_entry_ids,
                            "user_entry_id": user_entry_id,
                        },
                        "token_usage_history": self.agent.token_usage_history,
                    }
                    yield enriched_event
                    continue
                
                yield event
            
            if not self._stop_requested:
                yield {"type": "stream.finished", "agent_name": "Agent", "content": {}}
                
        except Exception as e:
            print(f"[APP] Error running agent: {e}")
            import traceback
            traceback.print_exc()
            yield {
                "type": "error",
                "agent_name": "System",
                "content": {"message": f"Error: {str(e)}"}
            }
            yield {"type": "stream.finished", "agent_name": "System", "content": {}}
    
    def get_chat_history(self, chat_id: str = "default") -> List[Dict]:
        """Get chat history (unwrapped, for API use)."""
        return self.history_manager.get_history(chat_id=chat_id)
    
    def get_wrapped_chat_history(self, chat_id: str = "default") -> List[Dict]:
        """Get wrapped chat history with IDs and metadata (for UI display)."""
        return self.history_manager.get_wrapped_history()
    
    def delete_messages_from_id(self, entry_id: str, chat_id: str = "default") -> Dict[str, Any]:
        """Delete a message and all subsequent messages.
        
        Args:
            entry_id: The ID of the message to start deletion from
            chat_id: Chat session ID (for future multi-chat support)
            
        Returns:
            Dict with status, deleted_count, and remaining_count
        """
        wrapped = self.history_manager.get_wrapped_history()
        
        # Find the index of the target entry
        target_idx = None
        for i, entry in enumerate(wrapped):
            if entry.get("id") == entry_id:
                target_idx = i
                break
        
        if target_idx is None:
            return {"status": "error", "message": "Entry not found", "deleted_count": 0}
        
        # Get IDs to delete (from target_idx to end)
        ids_to_delete = [e["id"] for e in wrapped[target_idx:]]
        result = self.history_manager.delete_entries(ids_to_delete)
        print(f"[APP] Deleted {result.get('deleted_count', 0)} messages from ID {entry_id}")
        return result
    
    def clear_chat_history(self, chat_id: str = "default") -> bool:
        """Clear chat history."""
        return self.history_manager.clear_history(chat_id=chat_id)
    
    def set_chat_history(self, history: List[Dict], chat_id: str = "default") -> Dict[str, Any]:
        """Replace chat history with new data (e.g., from loaded file).
        
        Args:
            history: List of wrapped history entries to save
            chat_id: Chat session ID (for future multi-chat support)
            
        Returns:
            Dict with 'status' key ('success' or 'error')
        """
        try:
            self.history_manager.history = history
            self.history_manager.save()
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
    
    # === VectorDB / Documents Methods ===
    
    def get_collections(self) -> List[Dict[str, Any]]:
        """Get all document collections.
        
        Returns:
            List of collection info dicts
        """
        if not self.vectordb_manager:
            return []
        try:
            return self.vectordb_manager.get_collections()
        except Exception as e:
            print(f"[APP] Error getting collections: {e}")
            return []
    
    def get_collection_metadata(self, name: str) -> Optional[Dict[str, Any]]:
        """Get metadata for a specific collection.
        
        Args:
            name: Collection name
            
        Returns:
            Collection metadata dict or None
        """
        if not self.vectordb_manager:
            return None
        try:
            return self.vectordb_manager.get_collection_metadata(name)
        except Exception as e:
            print(f"[APP] Error getting collection metadata: {e}")
            return None
    
    def create_collection(
        self,
        name: str,
        description: str = "",
        source_type: str = "document",
        tags: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """Create a new collection.
        
        Args:
            name: Collection name
            description: Human-readable description
            source_type: Type of source
            tags: Optional tags
            
        Returns:
            Result dict with status
        """
        if not self.vectordb_manager:
            return {"status": "error", "message": "VectorDB not initialized"}
        try:
            return self.vectordb_manager.create_collection(name, description, source_type, tags)
        except Exception as e:
            return {"status": "error", "message": str(e)}
    
    def delete_collection(self, name: str) -> Dict[str, Any]:
        """Delete a collection.
        
        Args:
            name: Collection name
            
        Returns:
            Result dict with status
        """
        if not self.vectordb_manager:
            return {"status": "error", "message": "VectorDB not initialized"}
        try:
            return self.vectordb_manager.delete_collection(name)
        except Exception as e:
            return {"status": "error", "message": str(e)}
    
    def update_collection_metadata(
        self,
        name: str,
        description: Optional[str] = None,
        tags: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """Update collection metadata.
        
        Args:
            name: Collection name
            description: New description (optional)
            tags: New tags (optional)
            
        Returns:
            Result dict with status
        """
        if not self.vectordb_manager:
            return {"status": "error", "message": "VectorDB not initialized"}
        try:
            return self.vectordb_manager.update_collection_metadata(name, description, tags)
        except Exception as e:
            return {"status": "error", "message": str(e)}
    
    def get_collection_documents(
        self,
        collection_name: str,
        limit: Optional[int] = None,
        offset: int = 0
    ) -> Dict[str, Any]:
        """Get documents from a collection.
        
        Args:
            collection_name: Name of the collection
            limit: Maximum documents to return
            offset: Number to skip
            
        Returns:
            Dict with status and documents list
        """
        if not self.vectordb_manager:
            return {"status": "error", "message": "VectorDB not initialized"}
        try:
            return self.vectordb_manager.get_collection_documents(
                collection_name, limit=limit, offset=offset, include_embeddings=False
            )
        except Exception as e:
            return {"status": "error", "message": str(e)}
    
    def chunk_document(
        self,
        file_path: str,
        chunk_size: int = 1000,
        chunk_overlap: int = 200
    ) -> Dict[str, Any]:
        """Chunk a document into smaller pieces.
        
        Args:
            file_path: Path to the document
            chunk_size: Max chunk size
            chunk_overlap: Overlap between chunks
            
        Returns:
            Dict with status and chunks list
        """
        if not self.vectordb_manager:
            return {"status": "error", "message": "VectorDB not initialized"}
        try:
            chunks = self.vectordb_manager.chunk_document(file_path, chunk_size, chunk_overlap)
            return {"status": "success", "chunks": chunks, "count": len(chunks)}
        except Exception as e:
            return {"status": "error", "message": str(e)}
    
    def add_documents_to_collection(
        self,
        collection_name: str,
        documents: List[str],
        metadatas: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        """Add documents to a collection with embeddings.
        
        This creates embeddings via OpenAI API which may take time.
        
        Args:
            collection_name: Name of the collection
            documents: List of text documents
            metadatas: Optional metadata for each document
            
        Returns:
            Dict with status and count
        """
        if not self.vectordb_manager:
            return {"status": "error", "message": "VectorDB not initialized"}
        if not self.vectordb_manager.openai_client:
            return {"status": "error", "message": "OpenAI client not configured. Please set API key in Settings."}
        try:
            return self.vectordb_manager.add_documents(collection_name, documents, metadatas)
        except Exception as e:
            return {"status": "error", "message": str(e)}
    
    def run(self):
        """Run the application."""
        # Create Qt app
        self.qt_app = QApplication(sys.argv)
        self.qt_app.setQuitOnLastWindowClosed(False)
        
        # Initialize agent and services
        self.initialize()
        
        # Import UI here to avoid circular imports
        from .ui import FloatingWidget
        
        # Create widget with app reference
        self.widget = FloatingWidget(app=self)
        
        # Show widget
        self.widget.show()
        
        # Run event loop
        return self.qt_app.exec()


def main():
    """Main entry point."""
    app = Application()
    sys.exit(app.run())


if __name__ == "__main__":
    main()
