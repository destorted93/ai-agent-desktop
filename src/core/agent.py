"""AI Agent implementation with streaming and tool support."""

import json
from datetime import datetime
from typing import Optional, List, Dict, Any, Generator, Callable
from openai import OpenAI

from ..config import AgentConfig


def make_serializable(obj: Any) -> Any:
    """Convert an object to a JSON-serializable format."""
    if obj is None:
        return None
    if isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, (list, tuple)):
        return [make_serializable(item) for item in obj]
    if isinstance(obj, dict):
        return {str(k): make_serializable(v) for k, v in obj.items()}
    if hasattr(obj, "model_dump"):
        return make_serializable(obj.model_dump())
    if hasattr(obj, "__dict__"):
        return make_serializable(obj.__dict__)
    return str(obj)


class Agent:
    """AI Agent with streaming responses and tool execution."""
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        name: str = "Atlas",
        tools: Optional[List[Any]] = None,
        user_id: str = "default_user",
        config: Optional[AgentConfig] = None,
    ):
        """Initialize the AI Agent.
        
        Args:
            api_key: OpenAI API key
            base_url: Custom API base URL
            name: Agent display name
            tools: List of tool instances with 'schema' and 'run' attributes
            user_id: User identifier for caching
            config: Agent configuration
        """
        self.api_key = api_key
        self.base_url = base_url
        self.name = name
        self.tools = tools or []
        self.user_id = user_id
        self.config = config or AgentConfig()
        
        # Initialize OpenAI client (can be None if no API key yet)
        self.client: Optional[OpenAI] = None
        self._init_client()
        
        # Get system prompt from config
        self.instructions = self.config.get_system_prompt(self.name)
        
        # Build tool schemas
        self.tool_schemas = [tool.schema for tool in self.tools]
        
        # Token tracking
        self.token_usage = {
            "turn": 1,
            "input_tokens": 0,
            "cached_tokens": 0,
            "output_tokens": 0,
            "reasoning_tokens": 0,
            "total_tokens": 0,
        }
        self.token_usage_history: Dict[int, Dict] = {}
        
        # Run state
        self._stop_requested = False
        self._run_start_time: Optional[datetime] = None
        self.turn = 1
        self.chat_history_during_run: List[Dict] = []
        self.generated_images: List[Dict] = []
        self.function_call_detected = False
        
        if not self.user_id:
            raise ValueError("user_id must be a non-empty string")
    
    def _init_client(self) -> None:
        """Initialize the OpenAI client."""
        if not self.api_key:
            self.client = None
            return
        
        kwargs = {"api_key": self.api_key}
        if self.base_url:
            kwargs["base_url"] = self.base_url
        
        try:
            self.client = OpenAI(**kwargs)
        except Exception:
            self.client = None
    
    def update_client(self, api_key: Optional[str] = None, base_url: Optional[str] = None) -> None:
        """Update the OpenAI client with new credentials."""
        if api_key is not None:
            self.api_key = api_key
        if base_url is not None:
            self.base_url = base_url
        self._init_client()
    
    def update_api_key(self, api_key: str, base_url: Optional[str] = None) -> None:
        """Update the API key and optionally the base URL. Alias for update_client."""
        self.update_client(api_key=api_key, base_url=base_url)
    
    def update_config(self, config: AgentConfig) -> None:
        """Update agent configuration."""
        self.config = config
        self.instructions = self.config.get_system_prompt(self.name)
    
    def update_tools(self, tools: List[Any]) -> None:
        """Update the available tools."""
        self.tools = tools
        self.tool_schemas = [tool.schema for tool in self.tools]
    
    def stop(self) -> None:
        """Request to stop the current agent run."""
        self._stop_requested = True
    
    def run(
        self,
        message: Optional[str] = None,
        input_messages: Optional[List[Dict]] = None,
        max_turns: Optional[int] = None,
        screenshots_b64: Optional[List[str]] = None,
        images: Optional[List[str]] = None,
        files: Optional[List[str]] = None,
        chat_id: Optional[str] = None,
    ) -> Generator[Dict[str, Any], None, None]:
        """Run the agent with a message and return event stream.
        
        Args:
            message: User message text
            input_messages: Existing chat history
            max_turns: Maximum turns (overrides config)
            screenshots_b64: List of base64-encoded screenshots (alias: images)
            images: Alias for screenshots_b64
            files: List of file paths to include as context
            chat_id: Optional chat session ID (for future multi-chat support)
            
        Yields:
            Event dictionaries with type and content
        """
        # Handle parameter aliases
        if images and not screenshots_b64:
            screenshots_b64 = images
        # Initialize run state
        self.chat_history_during_run = []
        self.function_call_detected = False
        self.turn = 1
        self._run_start_time = datetime.now()
        self._stop_requested = False
        self.generated_images = []
        
        max_turns = max_turns or self.config.max_turns
        input_messages = input_messages or []
        
        # Validate input
        if message is None and screenshots_b64 is None:
            yield self._done_event("No user input provided.")
            return
        
        if message is not None and not isinstance(message, str):
            yield self._done_event("Invalid input type.")
            return
        
        if not self.client:
            yield self._done_event("No API client configured. Please set API key.")
            return
        
        # Agent loop
        while True:
            # Check stop request
            if self._stop_requested:
                yield self._done_event("Agent stopped by user request.", stopped=True)
                return
            
            # Check turn limit
            if self.turn > max_turns:
                yield self._done_event(f"Max turns exceeded ({max_turns}).")
                return
            
            # Check if we should continue
            if self.turn > 1 and not self.function_call_detected:
                yield self._done_event("Agent run completed.")
                return
            
            # Build user message on first turn
            if self.turn == 1:
                content = []
                
                # Add file contents if provided
                if files:
                    file_context_parts = []
                    for file_path in files:
                        try:
                            import os
                            if os.path.isfile(file_path):
                                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                                    file_content = f.read()
                                file_context_parts.append(f"--- File: {file_path} ---\n{file_content}\n--- End of {file_path} ---")
                            elif os.path.isdir(file_path):
                                # For directories, list contents
                                file_list = []
                                for root, dirs, filenames in os.walk(file_path):
                                    for fname in filenames[:50]:  # Limit files
                                        file_list.append(os.path.join(root, fname))
                                file_context_parts.append(f"--- Directory: {file_path} ---\nFiles:\n" + "\n".join(file_list[:50]) + "\n--- End of directory ---")
                        except Exception as e:
                            file_context_parts.append(f"--- File: {file_path} (error reading: {e}) ---")
                    
                    if file_context_parts:
                        content.append({"type": "input_text", "text": "Attached files:\n\n" + "\n\n".join(file_context_parts) + "\n\n"})
                
                if message and message.strip():
                    content.append({"type": "input_text", "text": message})
                if screenshots_b64:
                    for screenshot in screenshots_b64:
                        content.append({
                            "type": "input_image",
                            "image_url": f"data:image/png;base64,{screenshot}",
                        })
                self.chat_history_during_run = [{"role": "user", "content": content}]
            
            # Reset function call flag
            self.function_call_detected = False
            
            # Make API request
            try:
                print(f"[DEBUG Agent] Starting turn {self.turn}, model: {self.config.model_name}")
                yield from self._process_turn(input_messages)
                print(f"[DEBUG Agent] Finished turn {self.turn}")
            except Exception as e:
                print(f"[DEBUG Agent] Error in turn {self.turn}: {e}")
                import traceback
                traceback.print_exc()
                yield {
                    "type": "response.error",
                    "agent_name": self.name,
                    "content": {"error": str(e)},
                }
                yield self._done_event(f"Error: {str(e)}")
                return
            
            self.turn += 1
    
    def _process_turn(self, input_messages: List[Dict]) -> Generator[Dict[str, Any], None, None]:
        """Process a single turn of the agent loop."""
        model = self.config.model_name
        temperature = self.config.temperature
        
        # Build request kwargs
        request_kwargs = {
            "model": model,
            "instructions": self.instructions,
            "input": input_messages + self.chat_history_during_run,
            "prompt_cache_key": self.user_id,
            "store": self.config.store,
            "stream": self.config.stream,
            "temperature": temperature,
            "tool_choice": self.config.tool_choice,
        }
        
        # Add tools if available
        if self.tool_schemas:
            request_kwargs["tools"] = self.tool_schemas
        
        # Add model-specific parameters only for gpt-5 models
        if model.startswith("gpt-5"):
            request_kwargs["reasoning"] = self.config.reasoning
            request_kwargs["text"] = self.config.text
            request_kwargs["include"] = self.config.include
        
        # Stream response
        print(f"[DEBUG Agent] Calling API with model={model}, stream={self.config.stream}")
        print(f"[DEBUG Agent] Input messages count: {len(input_messages + self.chat_history_during_run)}")
        events = self.client.responses.create(**request_kwargs)
        print(f"[DEBUG Agent] Got events iterator: {type(events)}")
        
        for event in events:
            if self._stop_requested:
                break
            
            event_type = getattr(event, "type", None)
            print(f"[DEBUG Agent] Raw event type: {event_type}")
            
            # Reasoning summary events
            if event_type == "response.reasoning_summary_part.added":
                yield {
                    "type": "response.reasoning_summary_part.added",
                    "agent_name": self.name,
                    "content": {},
                }
            
            elif event_type == "response.reasoning_summary_text.delta":
                yield {
                    "type": "response.reasoning_summary_text.delta",
                    "agent_name": self.name,
                    "content": {"delta": getattr(event, "delta", "")},
                }
            
            elif event_type == "response.reasoning_summary_text.done":
                yield {
                    "type": "response.reasoning_summary_text.done",
                    "agent_name": self.name,
                    "content": {"text": getattr(event, "text", "")},
                }
            
            # Content part added (text is about to start)
            elif event_type == "response.content_part.added":
                yield {
                    "type": "response.content_part.added",
                    "agent_name": self.name,
                    "content": {},
                }
            
            # Text output events
            elif event_type == "response.output_text.delta":
                yield {
                    "type": "response.output_text.delta",
                    "agent_name": self.name,
                    "content": {"delta": getattr(event, "delta", "")},
                }
            
            elif event_type == "response.output_text.done":
                yield {
                    "type": "response.output_text.done",
                    "agent_name": self.name,
                    "content": {"text": getattr(event, "text", "")},
                }
            
            # Output item done - important for function calls
            elif event_type == "response.output_item.done":
                item = getattr(event, "item", None)
                if item:
                    item_type = getattr(item, "type", None)
                    if item_type in ["function_call", "custom_tool_call"]:
                        yield {
                            "type": "response.output_item.done",
                            "agent_name": self.name,
                            "content": {"item": make_serializable(item)},
                        }
            
            # Image generation events
            elif event_type == "response.image_generation_call.generating":
                yield {
                    "type": "response.image_generation_call.generating",
                    "agent_name": self.name,
                    "content": {},
                }
            
            elif event_type == "response.image_generation_call.partial_image":
                yield {
                    "type": "response.image_generation_call.partial_image",
                    "agent_name": self.name,
                    "content": {"data": make_serializable(event)},
                }
            
            elif event_type == "response.image_generation_call.completed":
                yield {
                    "type": "response.image_generation_call.completed",
                    "agent_name": self.name,
                    "content": {"data": make_serializable(event)},
                }
            
            # Response completed - process output items and token usage
            elif event_type == "response.completed":
                response = getattr(event, "response", None)
                if response:
                    # Extract token usage
                    usage = getattr(response, "usage", None)
                    if usage:
                        input_details = getattr(usage, "input_tokens_details", None)
                        cached_tokens = getattr(input_details, "cached_tokens", 0) if input_details else 0
                        
                        output_details = getattr(usage, "output_tokens_details", None)
                        reasoning_tokens = getattr(output_details, "reasoning_tokens", 0) if output_details else 0
                        
                        self.token_usage = {
                            "turn": self.turn,
                            "input_tokens": getattr(usage, "input_tokens", 0),
                            "cached_tokens": cached_tokens,
                            "output_tokens": getattr(usage, "output_tokens", 0),
                            "reasoning_tokens": reasoning_tokens,
                            "total_tokens": getattr(usage, "total_tokens", 0),
                        }
                        self.token_usage_history[self.turn] = self.token_usage.copy()
                    
                    # Process output items (function calls, text, reasoning, etc.)
                    output = getattr(response, "output", [])
                    for output_item in output:
                        item_type = getattr(output_item, "type", None)
                        
                        if item_type == "function_call":
                            self.function_call_detected = True
                            
                            call_id = getattr(output_item, "call_id", "")
                            func_name = getattr(output_item, "name", "")
                            func_args_str = getattr(output_item, "arguments", "{}")
                            
                            # Execute the tool
                            try:
                                func_args = json.loads(func_args_str) if func_args_str else {}
                                result = None
                                for tool in self.tools:
                                    tool_name = tool.schema.get("name") if isinstance(tool.schema, dict) else getattr(tool.schema, "name", None)
                                    if tool_name == func_name:
                                        result = tool.run(**func_args)
                                        break
                                if result is None:
                                    result = {"error": f"Tool '{func_name}' not found"}
                            except Exception as e:
                                result = {"error": f"Tool execution error: {str(e)}"}
                            
                            # Append to chat history
                            self.chat_history_during_run.append(make_serializable(output_item))
                            self.chat_history_during_run.append({
                                "type": "function_call_output",
                                "call_id": call_id,
                                "output": json.dumps(make_serializable(result)),
                            })
                        
                        elif item_type == "reasoning":
                            final_item = make_serializable(output_item)
                            final_item.pop("status", None)
                            self.chat_history_during_run.append(final_item)
                        
                        elif item_type == "message":
                            self.chat_history_during_run.append(make_serializable(output_item))
                    
                    # Yield token usage event
                    if self.token_usage:
                        yield {
                            "type": "response.usage",
                            "agent_name": self.name,
                            "content": self.token_usage,
                        }
    
    def _done_event(self, message: str, stopped: bool = False) -> Dict[str, Any]:
        """Create a done event."""
        duration = (datetime.now() - self._run_start_time).total_seconds() if self._run_start_time else 0
        return {
            "type": "response.agent.done",
            "agent_name": self.name,
            "content": {
                "message": message,
                "duration_seconds": duration,
                "chat_history": self.chat_history_during_run,
                "generated_images": self.generated_images,
                "stopped": stopped,
            },
        }
