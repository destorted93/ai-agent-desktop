"""Base class for agent tools."""

from abc import ABC, abstractmethod
from typing import Any, Dict


class BaseTool(ABC):
    """Base class for all agent tools."""
    
    @property
    @abstractmethod
    def schema(self) -> Dict[str, Any]:
        """Return the tool schema for the OpenAI API."""
        pass
    
    @abstractmethod
    def run(self, **kwargs) -> Any:
        """Execute the tool with the given arguments."""
        pass
