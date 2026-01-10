import os
import json
from datetime import datetime
from typing import Optional
from openai import OpenAI
from .config import AgentConfig

class Agent:
    def __init__(self, api_key=None, base_url=None, name=None, tools=[], user_id="default_user", config: Optional[AgentConfig] = None):
        """AI Agent wrapper.

        Parameters:
            api_key: OpenAI API key.
            base_url: OpenAI API base URL.
            name: Agent display/name identifier.
            tools: Iterable of tool objects exposing a 'schema' attribute and 'run' method.
            user_id: Required unique user identifier (used for caching, etc.).
            config: Optional AgentConfig instance. If omitted, a default AgentConfig() is created.
        """
        self.api_key = api_key
        self.base_url = base_url
        self.name = name
        self.tools = tools
        self.user_id = user_id
        self.config = config or AgentConfig()

        client_kwargs = {}
        if api_key:
            client_kwargs["api_key"] = api_key
        if base_url:
            client_kwargs["base_url"] = base_url

        if api_key:
            self.client = OpenAI(**client_kwargs)
        else:
            self.client = None

        # Use config's system prompt (supports custom template modifications)
        self.instructions = self.config.get_system_prompt(self.name)
        self.tool_schemas = [tool.schema for tool in self.tools]
        self.token_usage = {
            "turn": 1,
            "input_tokens": 0,
            "cached_tokens": 0,
            "output_tokens": 0,
            "reasoning_tokens": 0,
            "total_tokens": 0
        }
        self.token_usage_history = {}
        self.function_call_detected = False
        self.turn = 1
        self.chat_history_during_run = []  # per-run ephemeral history additions
        self.generated_images = []
        self._stop_requested = False  # Flag to stop the current run

        if not self.user_id or not isinstance(self.user_id, str):
            raise ValueError("user_id must be a non-empty string.")
        
    def update_client(self, api_key: Optional[str] = "", base_url: Optional[str] = ""):
        """Update the OpenAI client with new API key and/or base URL."""

        self.api_key = api_key
        self.base_url = base_url
        
        client_kwargs = {}

        if api_key:
            client_kwargs["api_key"] = self.api_key
        if base_url:
            client_kwargs["base_url"] = self.base_url

        if api_key:
            self.client = OpenAI(**client_kwargs)
        else:
            self.client = None

    def color_text(self, text, color_code):
        # Windows PowerShell supports ANSI escape codes in recent versions
        return f"\033[{color_code}m{text}\033[0m"

    def stop(self):
        """Request to stop the current agent run."""
        self._stop_requested = True

    def run(self, message=None, input_messages=None, max_turns=16, screenshots_b64=None):
        self.chat_history_during_run = []
        self.function_call_detected = False
        self.turn = 1
        self._run_start_time = datetime.now()
        self._stop_requested = False  # Reset stop flag at the start of each run

        # if messages or message None or is not string or is empty, return None
        if input_messages is None or (message is None and screenshots_b64 is None) or (message is not None and not isinstance(message, str)):
            yield {
                "type": "response.agent.done",
                "agent_name": self.name,
                "content":{
                    "message": "No user input provided or input is invalid.",
                    "duration_seconds": (datetime.now() - self._run_start_time).total_seconds(),
                    "chat_history": self.chat_history_during_run,
                    "generated_images": self.generated_images
                }
            }
            return

        # start the Agent loop
        while True:
            # Check if stop was requested
            if self._stop_requested:
                yield {
                    "type": "response.agent.done",
                    "agent_name": self.name,
                    "content": {
                        "message": "Agent run stopped by user request.",
                        "duration_seconds": (datetime.now() - self._run_start_time).total_seconds(),
                        "chat_history": self.chat_history_during_run,
                        "generated_images": self.generated_images,
                        "stopped": True
                    }
                }
                return
            
            # Guard against runaway loops (SDK would raise MaxTurnsExceeded)
            if self.turn > max_turns:
                yield {
                    "type": "response.agent.done",
                    "agent_name": self.name,
                    "content": {
                        "message": f"Max turns exceeded (max_turns={max_turns}).",
                        "duration_seconds": (datetime.now() - self._run_start_time).total_seconds(),
                        "chat_history": self.chat_history_during_run,
                        "generated_images": self.generated_images
                    }
                }
                return
            # if this is not the first turn and no function call was detected, break the agent loop
            if self.turn > 1 and not self.function_call_detected:
                yield {
                    "type": "response.agent.done",
                    "agent_name": self.name,
                    "content": {
                        "message": "Agent run completed without further user input or function calls.",
                        "duration_seconds": (datetime.now() - self._run_start_time).total_seconds(),
                        "chat_history": self.chat_history_during_run,
                        "generated_images": self.generated_images
                    }
                }
                return
            elif self.turn == 1:
                # Build content array with text and optional screenshots
                content = []
                if message and message.strip():
                    content.append({"type": "input_text", "text": message})
                if screenshots_b64:
                    # Add each screenshot as a separate input_image
                    for screenshot_b64 in screenshots_b64:
                        content.append({
                            "type": "input_image",
                            "image_url": f"data:image/png;base64,{screenshot_b64}",
                        })
                
                user_message = {
                    "role": "user",
                    "content": content
                }
                self.chat_history_during_run = [user_message]

            # clear the function call detection flag for the next turn
            self.function_call_detected = False

            # Effective settings come straight from the config object
            model = self.config.model_name
            temperature = self.config.temperature
            reasoning = self.config.reasoning
            text = self.config.text
            store = self.config.store
            stream = self.config.stream
            tool_choice = self.config.tool_choice
            include = self.config.include
            prompt_cache_key = self.user_id

            # wrap the request in try except
            try:
                # exclude text and reasoning if a model's name does not start with "gpt-5"
                if not model.startswith("gpt-5"):
                    reasoning = None
                    text = None
                    include = []
                
                events = self.client.responses.create(
                    model=model,
                    instructions=self.instructions,
                    input=input_messages + self.chat_history_during_run,
                    prompt_cache_key=prompt_cache_key,
                    store=store,
                    stream=stream,
                    reasoning=reasoning,
                    text=text,
                    temperature=temperature,
                    tool_choice=tool_choice,
                    tools=self.tool_schemas,
                    include=include,
                    # service_tier="priority"
                )
                for event in events:
                    # Check for stop request before processing each event
                    if self._stop_requested:
                        yield {
                            "type": "response.agent.done",
                            "agent_name": self.name,
                            "content": {
                                "message": "Agent run stopped by user request.",
                                "duration_seconds": (datetime.now() - self._run_start_time).total_seconds(),
                                "chat_history": self.chat_history_during_run,
                                "generated_images": self.generated_images,
                                "stopped": True
                            }
                        }
                        return
                    
                    if event.type == "response.reasoning_summary_part.added":
                        yield {
                            "type": "response.reasoning_summary_part.added", 
                            "agent_name": self.name, 
                            "content": {}
                        }
                    elif event.type == "response.reasoning_summary_text.delta":
                        yield {
                            "type": "response.reasoning_summary_text.delta", 
                            "agent_name": self.name, 
                            "content": {
                                "delta": event.delta
                            }
                        }
                    elif event.type == "response.reasoning_summary_text.done":
                        yield {
                            "type": "response.reasoning_summary_text.done", 
                            "agent_name": self.name, 
                            "content": {
                                "text": event.text
                            }
                        }
                    elif event.type == "response.content_part.added":
                        yield {
                            "type": "response.content_part.added", 
                            "agent_name": self.name,
                            "content": {}
                        }
                    elif event.type == "response.output_text.delta":
                        yield {
                            "type": "response.output_text.delta", 
                            "agent_name": self.name, 
                            "content": {
                                "delta": event.delta
                            }
                        }
                    elif event.type == "response.output_text.done":
                        yield {
                            "type": "response.output_text.done", 
                            "agent_name": self.name, 
                            "content": {
                                "text": event.text
                            }
                        }
                    elif event.type == "response.output_item.done":
                        if event.item.type in ["function_call", "custom_tool_call"]:
                            yield {
                                "type": "response.output_item.done", 
                                "agent_name": self.name, 
                                "content": {
                                    "item": event.item
                                }
                            }
                    elif event.type == "response.image_generation_call.generating":
                        yield {
                            "type": "response.image_generation_call.generating", 
                            "agent_name": self.name,
                            "content": {}
                        }
                    elif event.type == "response.image_generation_call.partial_image":
                        yield {
                            "type": "response.image_generation_call.partial_image", 
                            "agent_name": self.name, 
                            "content": {
                                "data": event
                            }
                        }
                    elif event.type == "response.image_generation_call.completed":
                        yield {
                            "type": "response.image_generation_call.completed", 
                            "agent_name": self.name, 
                            "content": {
                                "data": event
                            }
                        }
                    elif event.type == "response.completed":
                        # Collect token usage for this turn
                        self.token_usage = {
                            "turn": self.turn,
                            "input_tokens": event.response.usage.input_tokens,
                            "cached_tokens": event.response.usage.input_tokens_details.cached_tokens,
                            "output_tokens": event.response.usage.output_tokens,
                            "reasoning_tokens": event.response.usage.output_tokens_details.reasoning_tokens,
                            "total_tokens": event.response.usage.total_tokens
                        }
                        # Retain token usage for this turn
                        self.token_usage_history[self.turn] = self.token_usage

                        # Append the AI agent output items to the chat history
                        for output_item in event.response.output:
                            # Check if the output item is a function call
                            if output_item.type == "function_call":
                                # Check if a function call was detected
                                self.function_call_detected = True

                                # Get the function call details
                                function_call = output_item
                                function_call_id = function_call.call_id
                                function_call_arguments = json.loads(function_call.arguments)
                                function_call_name = function_call.name
                                function_call_result = None

                                try:
                                    # Find and run the correct tool
                                    for tool in self.tools:
                                        if tool.schema["name"] == function_call_name:
                                            function_call_result = tool.run(**function_call_arguments)
                                            break
                                except Exception as e:
                                    function_call_result = {"type": "error", "message": f"Error occurred while calling function {function_call_name}: {e}"}

                                # Append the function call and its result to the chat history
                                self.chat_history_during_run.append(make_serializable(function_call))
                                self.chat_history_during_run.append({
                                    "type": "function_call_output",
                                    "call_id": function_call_id,
                                    "output": json.dumps(function_call_result),
                                })

                            elif output_item.type == "custom_tool_call":
                                # Append the custom tool call output item to the chat history
                                # self.chat_history_during_run.append(output_item)
                                pass

                            elif output_item.type == "reasoning":
                                # Append the reasoning output item to the chat history
                                final_item = make_serializable(output_item)
                                # remove status from final_item
                                final_item.pop("status", None)
                                self.chat_history_during_run.append(make_serializable(final_item))
                                pass

                            elif output_item.type == "message":
                                # Append the assistant message output item to the chat history
                                self.chat_history_during_run.append(make_serializable(output_item))

                            elif output_item.type == "image_generation_call":
                                # Handle image generation call output item
                                base64_image = output_item.result
                                self.generated_images.append({
                                    "type": "input_image",
                                    "image_url": f"data:image/png;base64,{base64_image}",
                                })

                        yield {
                            "type": "response.completed", 
                            "agent_name": self.name, 
                            "content": {
                                "usage": self.token_usage
                            }
                        }

                    elif event.type == "error":
                        # Handle error output item
                        assistant_message_with_error = {
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": f"An error occurred: {event.message}",
                                }
                            ]
                        }
                        self.chat_history_during_run.append(make_serializable(assistant_message_with_error))

            except Exception as e:
                # Handle error output item
                assistant_message_with_error = {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "output_text",
                            "text": f"An error occurred: {str(e)}",
                        }
                    ]
                }
                self.chat_history_during_run.append(make_serializable(assistant_message_with_error))

                yield {
                    "type": "response.agent.done",
                    "agent_name": self.name,
                    "content": {
                        "message": f"An error occurred during agent run: {str(e)}",
                        "duration_seconds": (datetime.now() - self._run_start_time).total_seconds(),
                        "chat_history": self.chat_history_during_run,
                        "generated_images": self.generated_images
                    }
                }
                return 

            self.turn += 1

def make_serializable(obj):
    if hasattr(obj, '__dict__'):
        return {k: make_serializable(v) for k, v in obj.__dict__.items()}
    elif isinstance(obj, list):
        return [make_serializable(i) for i in obj]
    elif isinstance(obj, dict):
        return {k: make_serializable(v) for k, v in obj.items()}
    else:
        return obj
