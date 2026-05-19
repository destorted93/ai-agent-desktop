"""AI Agent implementation with streaming and tool support."""

import json
from datetime import datetime
from typing import Optional, List, Dict, Any, Generator, Callable
from openai import OpenAI

from ..appcore.config_manager import AgentRuntimeConfig


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
        name: str = "Aria",
        tools: Optional[List[Any]] = None,
        user_id: str = "default_user",
        config: Optional[AgentRuntimeConfig] = None,
        agent_id: Optional[str] = None,
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
        self.agent_id = agent_id
        self.tools = tools or []
        self.user_id = user_id
        self.config = config or AgentRuntimeConfig()
        
        # Initialize OpenAI client (can be None if no API key yet)
        self.client: Optional[OpenAI] = None
        self._init_client()
        
        # System prompt text is provided by ConfigManager (already resolved).
        self.instructions = (self.config.instructions or "")
        
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
        self.session_items_during_run: List[Dict] = []
        self.generated_images: List[Dict] = []
        self.function_call_detected = False
        # Wrapper-only metadata keyed by call_id (never sent back to OpenAI).
        self.wrap_meta_by_call_id: Dict[str, Dict[str, Any]] = {}
        # Wrapper-only metadata keyed by session item index (never sent back to OpenAI).
        # Used for injected messages (e.g. tool-injected user images) so the UI can render them
        # correctly without treating them as real user bubbles.
        self.wrap_meta_by_item_index: Dict[int, Dict[str, Any]] = {}

        # POC: auto-injected per-turn telemetry (only on tool turns).
        # Set by the app runner; default is off.
        self._auto_telemetry_inject_enabled: bool = False
        self._auto_telemetry_session_baseline: Dict[str, Any] = {}
        # Per-turn scratch (cleared at turn start)
        self._auto_telemetry_tool_call_ids_this_turn: List[str] = []
        self._auto_telemetry_subagents_this_turn: List[Dict[str, Any]] = []

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
    
    def update_config(self, config: AgentRuntimeConfig) -> None:
        """Update agent configuration."""
        self.config = config
        self.instructions = (self.config.instructions or "")
    
    def update_tools(self, tools: List[Any]) -> None:
        """Update the available tools."""
        self.tools = tools
        self.tool_schemas = [tool.schema for tool in self.tools]
    
    def stop(self) -> None:
        """Request to stop the current agent run."""
        self._stop_requested = True
    
    def run(
        self,
        message: Optional[Any] = None,
        input_messages: Optional[List[Dict]] = None,
        max_turns: Optional[int] = None,
        screenshots_b64: Optional[List[str]] = None,
        images: Optional[List[str]] = None,
        files: Optional[List[str]] = None,
        session_id: Optional[str] = None,
    ) -> Generator[Dict[str, Any], None, None]:
        """Run the agent with a message and return event stream.
        
        Args:
            message: User message text
            input_messages: Existing session messages
            max_turns: Maximum turns (overrides config)
            screenshots_b64: List of base64-encoded screenshots (alias: images)
            images: Alias for screenshots_b64
            files: List of file paths to include as context
            session_id: Optional chat session ID (for future multi-chat support)
            
        Yields:
            Event dictionaries with type and content
        """
        # Handle parameter aliases
        if images and not screenshots_b64:
            screenshots_b64 = images
        # Initialize run state
        self.session_items_during_run = []
        self.function_call_detected = False
        self.wrap_meta_by_call_id = {}
        self.wrap_meta_by_item_index = {}
        self.turn = 1
        self._run_start_time = datetime.now()
        self._stop_requested = False
        self.generated_images = []
        
        max_turns = max_turns or self.config.max_turns
        input_messages = input_messages or []

        # clear token usage history
        self.token_usage_history = {}
        
        # Validate input
        if message is None and screenshots_b64 is None:
            yield self._done_event("No user input provided.")
            return
        
        if message is not None and not isinstance(message, (str, list)):
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
                    file_paths_list = []
                    for file_path in files:
                        import os
                        if os.path.isfile(file_path):
                            file_paths_list.append(f"--- File: {file_path}")
                        elif os.path.isdir(file_path):
                            file_paths_list.append(f"--- Directory: {file_path}")
                    
                    if file_paths_list:
                        content.append({"type": "input_text", "text": "\nAttached files:\n" + "\n".join(file_paths_list) + "\n\n"})
                
                # Message content
                msg_items = None
                try:
                    if isinstance(message, list):
                        msg_items = []
                        for it in message:
                            if not isinstance(it, dict):
                                continue
                            t = it.get("type")
                            if t == "input_text" and isinstance(it.get("text"), str):
                                txt = it.get("text")
                                if isinstance(txt, str) and txt:
                                    msg_items.append({"type": "input_text", "text": txt})
                            elif t == "input_image" and isinstance(it.get("image_url"), str):
                                url = it.get("image_url")
                                if isinstance(url, str) and url:
                                    msg_items.append({"type": "input_image", "image_url": url})
                except Exception:
                    msg_items = None

                if isinstance(msg_items, list):
                    content.extend(msg_items)
                else:
                    if isinstance(message, str) and message.strip():
                        content.append({"type": "input_text", "text": message})
                if screenshots_b64:
                    for screenshot in screenshots_b64:
                        content.append({
                            "type": "input_image",
                            "image_url": f"data:image/png;base64,{screenshot}",
                        })
                self.session_items_during_run = [{"role": "user", "content": content}]
            

                # Wrapper-only structured attachments for UI/editing (kept out of OpenAI payload).
                try:
                    atts = []
                    for file_path in (files or []):
                        if not isinstance(file_path, str) or not file_path:
                            continue
                        import os
                        kind = "dir" if os.path.isdir(file_path) else "file"
                        atts.append({"kind": kind, "path": file_path})

                    if atts:
                        meta0 = self.wrap_meta_by_item_index.get(0) if isinstance(self.wrap_meta_by_item_index, dict) else None
                        meta0 = meta0 if isinstance(meta0, dict) else {}
                        meta0["attachments"] = make_serializable(atts)
                        self.wrap_meta_by_item_index[0] = make_serializable(meta0)
                except Exception:
                    pass
            # Reset function call flag
            self.function_call_detected = False
            # Telemetry scratch for this turn
            try:
                self._auto_telemetry_tool_call_ids_this_turn = []
                self._auto_telemetry_subagents_this_turn = []
            except Exception:
                pass
            
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

        # Runner marker (debug): this path uses the OpenAI Responses API.
        try:
            aid = str(getattr(self, "agent_id", "") or "").strip()
            nm = str(getattr(self, "name", "") or "").strip() or "Agent"
            print(f"[DEBUG Agent] Runner: responses | agent={nm}{(' ('+aid+')') if aid else ''}")
            ins = str(getattr(self, "instructions", "") or "")
            ins_one = " ".join(ins.split())
            if ins_one:
                print(f"[DEBUG Agent] Instructions (first 100): {ins_one[:100]}{'...' if len(ins_one) > 100 else ''}")
        except Exception:
            pass
        
        # Build request kwargs
        request_kwargs = {
            "model": model,
            "instructions": self.instructions,
            "input": input_messages + self.session_items_during_run,
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
        
        # Stream response and print all request_kwargs for debugging
        for k, v in request_kwargs.items():
            # print all kwargs with truncation for large lists/dicts. print onyl the first 100 chars of the value if it's a string, and indicate if it's truncated.
            if isinstance(v, str):
                if len(v) > 100:
                    print(f"[DEBUG Agent] Request param: {k} = {v[:100]}... (truncated, total length {len(v)})")
                else:
                    print(f"[DEBUG Agent] Request param: {k} = {v}")
            elif isinstance(v, (list, dict)):
                print(f"[DEBUG Agent] Request param: {k} = {json.dumps(v)[:100]}{'...' if len(json.dumps(v)) > 100 else ''}")
            else:
                print(f"[DEBUG Agent] Request param: {k} = {v}")
        
        print(f"[DEBUG Agent] Input messages count: {len(input_messages + self.session_items_during_run)}")
        


        # Make the API call using responses API
        events = self.client.responses.create(**request_kwargs)
        
        print(f"[DEBUG Agent] Got events iterator: {type(events)}")
        
        for event in events:
            if self._stop_requested:
                break
            
            event_type = getattr(event, "type", None)
            # print(f"[DEBUG Agent] Raw event type: {event_type}")
            
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
                                        # Internal-only call context for tools that need to stream UI events
                                        # or attach subhistory links (e.g., consult_ariane).
                                        try:
                                            if str(func_name) in ("consult_ariane", "run_subagent"):
                                                func_args["_call_id"] = str(call_id or "")
                                                func_args["_parent_stream_topic"] = getattr(self, "_active_stream_topic", None)
                                                func_args["_parent_run_id"] = getattr(self, "_active_run_id", None)
                                                func_args["_parent_session_id"] = getattr(self, "_active_session_id", None)
                                        except Exception:
                                            pass

                                        result = tool.run(**func_args)
                                        break
                                if result is None:
                                    result = {"error": f"Tool '{func_name}' not found"}
                            except Exception as e:
                                result = {"error": f"Tool execution error: {str(e)}"}

                            # Wrapper-only side-channel meta (kept out of OpenAI input/output items).
                            wrap_meta = None
                            if isinstance(result, dict) and "__wrap_meta__" in result:
                                try:
                                    wrap_meta = result.pop("__wrap_meta__")
                                except Exception:
                                    wrap_meta = None

                            # Support list-returning tools: allow a meta-only dict element.
                            if wrap_meta is None and isinstance(result, list):
                                try:
                                    idx_to_drop = None
                                    for i2, it in enumerate(result):
                                        if isinstance(it, dict) and "__wrap_meta__" in it:
                                            wrap_meta = it.pop("__wrap_meta__")
                                            # If the dict only contained meta, drop it from the output list.
                                            if len(it.keys()) == 0:
                                                idx_to_drop = i2
                                            break
                                    if idx_to_drop is not None:
                                        try:
                                            result.pop(idx_to_drop)
                                        except Exception:
                                            pass
                                except Exception:
                                    wrap_meta = None

                            # Internal injected message (priority checkpoint for the next turn).
                            inject_message = None
                            if isinstance(result, dict) and "__inject_message__" in result:
                                try:
                                    inject_message = result.pop("__inject_message__")
                                except Exception:
                                    inject_message = None

                            # Keep `inner_loop` model-facing output boring on purpose.
                            # The injected message is the real payload; the function_call_output should not distract/confuse.
                            if str(func_name) == "inner_loop" and isinstance(result, dict) and "error" not in result:
                                result = {"status": "success"}

                            if isinstance(wrap_meta, dict) and call_id:
                                self.wrap_meta_by_call_id[str(call_id)] = make_serializable(wrap_meta)

                            # Telemetry: remember what happened this turn.
                            try:
                                if call_id:
                                    self._auto_telemetry_tool_call_ids_this_turn.append(str(call_id))
                                if isinstance(wrap_meta, dict):
                                    su = wrap_meta.get("subagent_usage")
                                    if isinstance(su, dict):
                                        self._auto_telemetry_subagents_this_turn.append(make_serializable(su))
                            except Exception:
                                pass

                            # Emit a UI event for tool output (so the chat can show it immediately).
                            try:
                                payload = {
                                    "call_id": call_id,
                                    "name": func_name,
                                    "arguments": func_args_str,
                                    "output": make_serializable(result),
                                }
                                if isinstance(wrap_meta, dict):
                                    payload["wrap_meta"] = make_serializable(wrap_meta)
                                yield {
                                    "type": "response.tool_output",
                                    "agent_name": self.name,
                                    "content": payload,
                                }
                            except Exception:
                                pass

                            # Append to session (OpenAI-clean items only)
                            self.session_items_during_run.append(make_serializable(output_item))
                            self.session_items_during_run.append({
                                "type": "function_call_output",
                                "call_id": call_id,
                                "output": json.dumps(make_serializable(result)),
                            })

                            # Append any injected checkpoint message AFTER the tool output, so it is the freshest context.
                            if inject_message is not None:
                                try:
                                    self.session_items_during_run.append(make_serializable(inject_message))
                                except Exception:
                                    pass


                                # Persist wrapper-only meta for injected messages so the UI can render them
                                # as "injected" (not as a real user bubble) after reload/restart.
                                try:
                                    inj_role = str(inject_message.get("role") or "").lower() if isinstance(inject_message, dict) else ""
                                    inj_types = []
                                    try:
                                        for p in (inject_message.get("content") or []):
                                            if isinstance(p, dict) and isinstance(p.get("type"), str):
                                                inj_types.append(p.get("type"))
                                    except Exception:
                                        inj_types = []

                                    inj_meta = {
                                        "injected": True,
                                        "origin_tool_call_id": (str(call_id) if call_id else None),
                                        "origin_tool_name": str(func_name),
                                        "injected_role": inj_role,
                                        "content_types": inj_types,
                                    }

                                    inj_idx = len(self.session_items_during_run) - 1
                                    self.wrap_meta_by_item_index[int(inj_idx)] = make_serializable(inj_meta)

                                    # Stream user-role injections to the UI immediately (so they appear live,
                                    # without requiring a session reload).
                                    if inj_role == "user":
                                        yield {
                                            "type": "response.injected_message",
                                            "agent_name": self.name,
                                            "content": {
                                                "origin_call_id": (str(call_id) if call_id else None),
                                                "origin_tool_name": str(func_name),
                                                "message": make_serializable(inject_message),
                                                "wrap_meta": make_serializable(inj_meta),
                                            },
                                        }
                                except Exception:
                                    pass
                                # If the injected message is assistant-role, also stream it to the UI immediately.
                                # (So you can *see* your self-talk/checkpoints during the same run.)
                                try:
                                    if isinstance(inject_message, dict) and str(inject_message.get("role") or "").lower() == "assistant":
                                        txt_parts = []
                                        for p in (inject_message.get("content") or []):
                                            if isinstance(p, dict) and isinstance(p.get("text"), str):
                                                txt_parts.append(p.get("text"))
                                        injected_text = "".join(txt_parts).strip()
                                        if injected_text:
                                            yield {
                                                "type": "response.content_part.added",
                                                "agent_name": self.name,
                                                "content": {},
                                            }
                                            yield {
                                                "type": "response.output_text.delta",
                                                "agent_name": self.name,
                                                "content": {"delta": injected_text},
                                            }
                                            yield {
                                                "type": "response.output_text.done",
                                                "agent_name": self.name,
                                                "content": {"text": injected_text},
                                            }
                                except Exception:
                                    pass
                        
                        elif item_type == "reasoning":
                            final_item = make_serializable(output_item)
                            final_item.pop("status", None)
                            self.session_items_during_run.append(final_item)
                        
                        elif item_type == "message":
                            self.session_items_during_run.append(make_serializable(output_item))
                    
                    # Auto-inject telemetry after tool turns (POC: blunt, full payload).
                    try:
                        if bool(getattr(self, "_auto_telemetry_inject_enabled", False)) and bool(self.function_call_detected):
                            inj_msg, inj_meta, ui_ev = self._build_turn_telemetry_injection()
                            if isinstance(inj_msg, dict):
                                self.session_items_during_run.append(make_serializable(inj_msg))
                                inj_idx = len(self.session_items_during_run) - 1
                                if isinstance(inj_meta, dict):
                                    self.wrap_meta_by_item_index[int(inj_idx)] = make_serializable(inj_meta)
                                if isinstance(ui_ev, dict):
                                    yield ui_ev
                    except Exception:
                        pass

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
                "session_items": self.session_items_during_run,
                "generated_images": self.generated_images,
                "stopped": stopped,
                # Wrapper-only metadata keyed by tool call_id (never sent to OpenAI).
                "wrap_meta_by_call_id": self.wrap_meta_by_call_id,
                # Wrapper-only metadata keyed by session item index (never sent to OpenAI).
                "wrap_meta_by_item_index": self.wrap_meta_by_item_index,
            },
            "token_usage_history": self.token_usage_history,
        }

    def _build_turn_telemetry_injection(self) -> Any:
        """Build a compact user-role __telemetry__ injected message + wrapper meta + UI event.

        Policy:
        - injected only on tool turns (caller enforces)
        - POC: keep it UI-visible (no survive filtering yet)
        - include: current turn usage (main + subagents called this turn), run usage so far, session usage so far
        """

        def _norm_tokens(d: Any) -> Dict[str, int]:
            d = d if isinstance(d, dict) else {}
            return {
                "input_tokens": int(d.get("input_tokens") or 0),
                "cached_tokens": int(d.get("cached_tokens") or 0),
                "output_tokens": int(d.get("output_tokens") or 0),
                "reasoning_tokens": int(d.get("reasoning_tokens") or 0),
                "total_tokens": int(d.get("total_tokens") or 0),
            }

        def _add(a: Dict[str, int], b: Dict[str, int]) -> Dict[str, int]:
            return {
                "input_tokens": int(a.get("input_tokens") or 0) + int(b.get("input_tokens") or 0),
                "cached_tokens": int(a.get("cached_tokens") or 0) + int(b.get("cached_tokens") or 0),
                "output_tokens": int(a.get("output_tokens") or 0) + int(b.get("output_tokens") or 0),
                "reasoning_tokens": int(a.get("reasoning_tokens") or 0) + int(b.get("reasoning_tokens") or 0),
                "total_tokens": int(a.get("total_tokens") or 0) + int(b.get("total_tokens") or 0),
            }

        # --- Turn usage (main + subagents called this turn) ---
        turn_main = _norm_tokens(self.token_usage)

        turn_sub_total = _norm_tokens({})
        turn_subagents: List[Dict[str, Any]] = []
        try:
            for su in (getattr(self, "_auto_telemetry_subagents_this_turn", None) or []):
                if not isinstance(su, dict):
                    continue
                nm = su.get("subagent_name") or su.get("subagent_id") or "subagent"
                tot = _norm_tokens(su.get("token_usage_totals"))
                turn_sub_total = _add(turn_sub_total, tot)
                turn_subagents.append({"name": nm, "token_usage_totals": tot})
        except Exception:
            turn_subagents = []

        turn_total = _add(turn_main, turn_sub_total)

        # --- Run usage so far ---
        turn_hist = self.token_usage_history if isinstance(self.token_usage_history, dict) else {}
        run_main_total = _norm_tokens({})
        for v in (turn_hist or {}).values():
            run_main_total = _add(run_main_total, _norm_tokens(v))

        run_sub_total = _norm_tokens({})
        try:
            for m in (self.wrap_meta_by_call_id or {}).values():
                if not isinstance(m, dict):
                    continue
                su = m.get("subagent_usage")
                if not isinstance(su, dict):
                    continue
                run_sub_total = _add(run_sub_total, _norm_tokens(su.get("token_usage_totals")))
        except Exception:
            pass

        run_total = _add(run_main_total, run_sub_total)

        # --- Session usage so far (baseline + this run so far) ---
        baseline = getattr(self, "_auto_telemetry_session_baseline", None)
        baseline = baseline if isinstance(baseline, dict) else {}

        base_total = _norm_tokens(baseline.get("token_stats_raw_totals"))
        base_main = _norm_tokens(baseline.get("token_stats_raw_totals_main"))
        base_sub = _norm_tokens(baseline.get("token_stats_raw_totals_subagents"))

        session_total = _add(base_total, run_total)
        session_main = _add(base_main, run_main_total)
        session_sub = _add(base_sub, run_sub_total)

        # Build short markdown (no JSON) to keep the injected message small.
        run_id = str(getattr(self, "_active_run_id", None) or "")

        def _fmt_turn(t: Dict[str, int]) -> str:
            return (
                f"{t.get('total_tokens', 0)} (in {t.get('input_tokens', 0)} c {t.get('cached_tokens', 0)} "
                f"out {t.get('output_tokens', 0)} r {t.get('reasoning_tokens', 0)})"
            )

        def _fmt_total(t: Dict[str, int]) -> str:
            # Full breakdown, same style as turn main.
            return _fmt_turn(t)

        # Run subagents breakdown (by name, across this run so far)
        run_sub_by_name: Dict[str, Dict[str, int]] = {}
        try:
            for m in (self.wrap_meta_by_call_id or {}).values():
                if not isinstance(m, dict):
                    continue
                su = m.get("subagent_usage")
                if not isinstance(su, dict):
                    continue
                nm = su.get("subagent_name") or su.get("subagent_id") or "subagent"
                cur = run_sub_by_name.get(str(nm))
                cur = cur if isinstance(cur, dict) else _norm_tokens({})
                run_sub_by_name[str(nm)] = _add(cur, _norm_tokens(su.get("token_usage_totals")))
        except Exception:
            pass

        run_sub_items = sorted(run_sub_by_name.items(), key=lambda kv: int((kv[1] or {}).get("total_tokens") or 0), reverse=True)
        if run_sub_items:
            top = run_sub_items[:2]
            more = len(run_sub_items) - len(top)
            run_sub_summary = ", ".join([f"{n} {_fmt_total(t)}" for n, t in top]) + (f" +{more}" if more > 0 else "")
        else:
            run_sub_summary = "-"

        if turn_subagents:
            turn_sub_summary = ", ".join([f"{x.get('name')} {_fmt_total(x.get('token_usage_totals') or {})}" for x in turn_subagents])
        else:
            turn_sub_summary = "-"

        lines = [
            "__telemetry__",
            f"- turn {int(getattr(self, 'turn', 0) or 0)}: main {_fmt_turn(turn_main)}; sub {turn_sub_summary}",
            f"- run_id={run_id or '-'}: total {_fmt_total(run_total)}; main {_fmt_total(run_main_total)}; sub {_fmt_total(run_sub_total)}; by {run_sub_summary}",
            f"- session: total {_fmt_total(session_total)}; main {_fmt_total(session_main)}; sub {_fmt_total(session_sub)}",
        ]
        txt = "\n".join(lines)
        inject_message = {"role": "user", "content": [{"type": "input_text", "text": txt}]}

        inj_meta = {
            "injected": True,
            "origin_tool_call_id": None,
            "origin_tool_name": "__auto_telemetry__",
            "injected_role": "user",
            "content_types": ["input_text"],
            "telemetry": True,
            "telemetry_level": "md_mini",
        }

        ui_ev = {
            "type": "response.injected_message",
            "agent_name": self.name,
            "content": {
                "origin_call_id": None,
                "origin_tool_name": "__auto_telemetry__",
                "message": make_serializable(inject_message),
                "wrap_meta": make_serializable(inj_meta),
            },
        }

        return inject_message, inj_meta, ui_ev
