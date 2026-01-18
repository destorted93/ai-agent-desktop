"""Agent configuration for OpenAI API calls."""

from typing import Optional, Dict, Any, List
from .prompts import get_system_prompt


class AgentConfig:
    """Configuration container for agent behavior and API parameters."""
    
    def __init__(
        self,
        model_name: str = "gpt-5",
        temperature: float = 1.0,
        max_turns: int = 32,
        reasoning: Optional[Dict[str, Any]] = None,
        text: Optional[Dict[str, Any]] = None,
        store: bool = False,
        stream: bool = True,
        tool_choice: str = "auto",
        include: Optional[List[str]] = None,
        system_prompt: Optional[str] = None,
    ):
        self.model_name = model_name
        self.temperature = temperature
        self.max_turns = max_turns
        self.reasoning = reasoning or {"effort": "medium", "summary": "auto"}
        self.text = text or {"verbosity": "low"}
        self.store = store
        self.stream = stream
        self.tool_choice = tool_choice
        self.include = include or ["reasoning.encrypted_content"]
        self._system_prompt_template = system_prompt
    
    def get_system_prompt(self, agent_name: str) -> str:
        """Get the formatted system prompt."""
        return get_system_prompt(agent_name, self._system_prompt_template)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "model_name": self.model_name,
            "temperature": self.temperature,
            "max_turns": self.max_turns,
            "reasoning": self.reasoning,
            "text": self.text,
            "store": self.store,
            "stream": self.stream,
            "tool_choice": self.tool_choice,
            "include": self.include,
        }
    
    @classmethod
    def from_settings(cls, settings) -> "AgentConfig":
        """Create AgentConfig from Settings object."""
        return cls(
            model_name=settings.agent.model_name,
            temperature=settings.agent.temperature,
            max_turns=settings.agent.max_turns,
            reasoning={
                "effort": settings.agent.reasoning_effort,
                "summary": settings.agent.reasoning_summary,
            },
            text={"verbosity": settings.agent.text_verbosity},
            stream=settings.agent.stream_responses,
            system_prompt=settings.agent.custom_system_prompt,
        )
