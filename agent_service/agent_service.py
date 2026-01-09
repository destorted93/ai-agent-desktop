
import os
import json
import threading
import requests
import asyncio
import websockets
from jsonschema import Draft7Validator, ValidationError
import pathlib

from .models import (
    ChatHistoryRequest,
    ChatHistoryResponse,
    DeleteHistoryRequest,
    DeleteHistoryRequestPayload,
    UpdateSettingsRequest,
    UpdateSettingsRequestPayload,
    ErrorResponse,
    OperationResponse
)

class AgentService:
    def __init__(self, agent_url=None):
        self.agent_url = agent_url or os.environ.get("AGENT_URL", "http://127.0.0.1:6002")
        self.ws_url = self.agent_url.replace("http://", "ws://").replace("https://", "wss://") + "/chat/ws"
        self.current_websocket = None
        self.stop_requested = False
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})


    def get_history(self, chat_id: str = "default") -> list:
        """Retrieve chat history from the agent service."""
        try:
            url = f"{self.agent_url}/chat/history"
            response = self.session.get(
                url,
                params={"chat_id": chat_id},
                timeout=5
            )

            response.raise_for_status()
        
            # Validate response
            validated = ChatHistoryResponse(**response.json())
            return validated.payload.entries
        
        except requests.HTTPError as http_err:
            print(f"HTTP error occurred: {http_err}")
            return []
        except requests.RequestException as req_err:
            print(f"Request error occurred: {req_err}")
            return []
        except Exception as e:
            print(f"Error getting history: {e}")
            return []

    def clear_history(self, chat_id: str = "default") -> bool:
        """Clear chat history on the agent service."""
        try:
            url = f"{self.agent_url}/chat/history"

            envelope = DeleteHistoryRequest(
                type="delete_history_request",
                payload=DeleteHistoryRequestPayload(chat_id=chat_id)
            )

            response = self.session.delete(
                url,
                json=envelope.model_dump(mode="json"),
                timeout=5
            )
            response.raise_for_status()

            # Validate response
            validated = OperationResponse(**response.json())

            return validated.payload.success

        except requests.HTTPError as http_err:
            print(f"HTTP error occurred: {http_err}")
            return False
        except requests.RequestException as req_err:
            print(f"Request error occurred: {req_err}")
            return False
        except Exception as e:
            print(f"Error getting history: {e}")
            return False

    def update_settings(self, settings: dict = {}) -> bool:
        """Update agent service settings."""
        try:
            url = f"{self.agent_url}/settings/update"

            envelope = UpdateSettingsRequest(
                type="update_settings_request",
                payload=UpdateSettingsRequestPayload(settings=settings)
            )

            response = self.session.put(
                url,
                json=envelope.model_dump(mode="json"),
                timeout=5
            )
            response.raise_for_status()

            # Validate response
            validated = OperationResponse(**response.json())

            return validated.payload.success

        except requests.HTTPError as http_err:
            print(f"HTTP error occurred: {http_err}")
            return False
        except requests.RequestException as req_err:
            print(f"Request error occurred: {req_err}")
            return False
        except Exception as e:
            print(f"Error updating settings: {e}")
            return False

    def close(self):
        """Close the session."""
        self.session.close()

    def __enter__(self):
        """Enter context manager."""
        return self
    
    def __exit__(self, exc_type, exc_value, traceback):
        """Exit context manager."""
        self.close()

# You can expand this class with more methods as needed for your protocol.
