"""Main application entry point for AI Agent Desktop."""

import sys
import os
import json
from typing import Optional, List, Generator, Dict, Any

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QObject, pyqtSignal

from .config import Settings, get_settings, AgentConfig
from .core import Agent
from .storage import ChatHistoryManager, MemoryManager, SecureStorage, get_secret
from .tools import get_default_tools
from .services import TranscribeService


class Application(QObject):
    """Main application class - the backbone/orchestrator."""
    
    # Signal to emit agent events to UI
    agent_event = pyqtSignal(dict)
    
    def __init__(self):
        super().__init__()
        self.settings = get_settings()
        self.history_manager = ChatHistoryManager()
        self.secure_storage = SecureStorage()
        self.agent: Optional[Agent] = None
        self.transcribe_service: Optional[TranscribeService] = None
        self.widget = None  # Will be set after UI import
        self.qt_app: Optional[QApplication] = None
        self._stop_requested = False
    
    def initialize(self):
        """Initialize the application."""
        # Get API key (optional - can be set later via UI)
        api_key = (
            self.settings.api_key 
            or os.getenv("OPENAI_API_KEY") 
            or get_secret("api_token")
            or self.secure_storage.get_secret("api_token")
        )
        
        # Get base URL from settings or secure storage
        base_url = self.settings.base_url or self.secure_storage.get_config_value("base_url")
        
        # Get project root
        project_root = self.settings.tools.project_root or os.getcwd()
        
        # Create agent config
        agent_config = AgentConfig.from_settings(self.settings)
        
        # Get tools
        tools = get_default_tools(
            project_root=project_root,
            permission_required=self.settings.tools.terminal_permission_required
        )
        
        # Create agent
        self.agent = Agent(
            api_key=api_key,
            base_url=base_url,
            name=self.settings.agent_name,
            tools=tools,
            user_id=self.settings.user_id,
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
    
    def update_api_key(self, api_key: str, base_url: Optional[str] = None):
        """Update API key and reinitialize agent."""
        if self.agent:
            self.agent.update_api_key(api_key, base_url)
        
        # Reinitialize transcribe service
        if api_key:
            try:
                from openai import OpenAI
                client = OpenAI(api_key=api_key, base_url=base_url) if base_url else OpenAI(api_key=api_key)
                self.transcribe_service = TranscribeService(client=client)
            except Exception:
                pass
    
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
                    
                    # Save chat history
                    chat_history = content.get("chat_history", [])
                    if chat_history:
                        try:
                            self.history_manager.append_entries(chat_history)
                            print(f"[APP] Saved {len(chat_history)} entries to chat history")
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
        """Get chat history."""
        return self.history_manager.get_history(chat_id=chat_id)
    
    def clear_chat_history(self, chat_id: str = "default") -> bool:
        """Clear chat history."""
        return self.history_manager.clear_history(chat_id=chat_id)
    
    def transcribe(self, audio_data: bytes, language: str = "en") -> Optional[Dict]:
        """Transcribe audio data."""
        if self.transcribe_service:
            return self.transcribe_service.transcribe(audio_data=audio_data, language=language)
        return None
    
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
