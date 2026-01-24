"""Agent configuration for OpenAI API calls."""

import os
import yaml
from pathlib import Path
from typing import Optional, Dict, Any, List


# Default agent configuration values (used when config.yaml missing or incomplete)
DEFAULT_MODEL = "gpt-5.1"
DEFAULT_TEMPERATURE = 1.0
DEFAULT_MAX_TURNS = 32
DEFAULT_REASONING_EFFORT = "medium"
DEFAULT_REASONING_SUMMARY = "auto"
DEFAULT_TEXT_VERBOSITY = "medium"
DEFAULT_STORE = False
DEFAULT_STREAM = True
DEFAULT_TOOL_CHOICE = "auto"
DEFAULT_SYSTEM_PROMPT_PATH = "prompts/system_prompt.md"


class AgentConfig:
    """Configuration container for agent behavior and API parameters."""
    
    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        temperature: float = DEFAULT_TEMPERATURE,
        max_turns: int = DEFAULT_MAX_TURNS,
        reasoning: Optional[Dict[str, Any]] = None,
        text: Optional[Dict[str, Any]] = None,
        store: bool = DEFAULT_STORE,
        stream: bool = DEFAULT_STREAM,
        tool_choice: str = DEFAULT_TOOL_CHOICE,
        include: Optional[List[str]] = None,
        system_prompt_path: str = DEFAULT_SYSTEM_PROMPT_PATH,
    ):
        self.model_name = model_name
        self.temperature = temperature
        self.max_turns = max_turns
        self.reasoning = reasoning or {"effort": DEFAULT_REASONING_EFFORT, "summary": DEFAULT_REASONING_SUMMARY}
        self.text = text or {"verbosity": DEFAULT_TEXT_VERBOSITY}
        self.store = store
        self.stream = stream
        self.tool_choice = tool_choice
        self.include = include or ["reasoning.encrypted_content"]
        self.system_prompt_path = system_prompt_path
    
    def get_system_prompt(self, agent_name: str) -> str:
        """Load and format system prompt from markdown file.
        
        Args:
            agent_name: Name to substitute in {agent_name} placeholder
            
        Returns:
            Formatted system prompt text
        """
        # Resolve path relative to project root
        prompt_path = Path(self.system_prompt_path)
        if not prompt_path.is_absolute():
            # Try relative to current directory first
            if prompt_path.exists():
                pass
            # Then try relative to this file's parent (src/config -> project root)
            elif (Path(__file__).parent.parent.parent / prompt_path).exists():
                prompt_path = Path(__file__).parent.parent.parent / prompt_path
            else:
                # Fallback to default
                print(f"Warning: System prompt not found at {self.system_prompt_path}, using default")
                return self._get_default_prompt(agent_name)
        
        try:
            prompt_template = prompt_path.read_text(encoding="utf-8")
            return prompt_template.format(agent_name=agent_name)
        except FileNotFoundError:
            print(f"Warning: System prompt not found at {prompt_path}, using default")
            return self._get_default_prompt(agent_name)
        except Exception as e:
            print(f"Error loading system prompt: {e}, using default")
            return self._get_default_prompt(agent_name)
    
    def _get_default_prompt(self, agent_name: str) -> str:
        """Minimal fallback prompt if file not found."""
        return f"You are {agent_name}, a helpful AI assistant."
    
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
    def from_yaml(cls, yaml_path: Optional[Path] = None) -> "AgentConfig":
        """Load AgentConfig from YAML file with fallback to defaults.
        
        This is the primary way to create AgentConfig - loads from config.yaml
        and falls back to DEFAULT_* constants for any missing values.
        """
        if yaml_path is None:
            yaml_path = Path("config.yaml")
            if not yaml_path.exists():
                # Try app data directory
                import os
                if os.name == "nt":
                    base = os.getenv("APPDATA", os.path.expanduser("~"))
                else:
                    base = os.getenv("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
                yaml_path = Path(base) / "ai-agent" / "config.yaml"
        
        # Start with defaults
        config_data = {
            "model_name": DEFAULT_MODEL,
            "temperature": DEFAULT_TEMPERATURE,
            "max_turns": DEFAULT_MAX_TURNS,
            "reasoning_effort": DEFAULT_REASONING_EFFORT,
            "reasoning_summary": DEFAULT_REASONING_SUMMARY,
            "text_verbosity": DEFAULT_TEXT_VERBOSITY,
            "stream_responses": DEFAULT_STREAM,
            "system_prompt_path": DEFAULT_SYSTEM_PROMPT_PATH,
        }
        
        # Load from YAML if exists
        if yaml_path.exists():
            try:
                with open(yaml_path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                    agent_data = data.get("agent", {})
                    # Override defaults with YAML values
                    if agent_data:
                        config_data.update(agent_data)
            except Exception as e:
                print(f"Warning: Could not load agent config from {yaml_path}: {e}")
        
        return cls(
            model_name=config_data.get("model_name", DEFAULT_MODEL),
            temperature=config_data.get("temperature", DEFAULT_TEMPERATURE),
            max_turns=config_data.get("max_turns", DEFAULT_MAX_TURNS),
            reasoning={
                "effort": config_data.get("reasoning_effort", DEFAULT_REASONING_EFFORT),
                "summary": config_data.get("reasoning_summary", DEFAULT_REASONING_SUMMARY),
            },
            text={"verbosity": config_data.get("text_verbosity", DEFAULT_TEXT_VERBOSITY)},
            stream=config_data.get("stream_responses", DEFAULT_STREAM),
            system_prompt_path=config_data.get("system_prompt_path", DEFAULT_SYSTEM_PROMPT_PATH),
        )
