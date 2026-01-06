import sys
import os
import asyncio

# Add parent directory to path so we can import agent, tools, etc.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime
from agent import Agent, AgentConfig
from agent.agent import make_serializable

from tools import (
    GetUserMemoriesTool,
    CreateUserMemoryTool,
    UpdateUserMemoryTool,
    DeleteUserMemoryTool,
    GetChatHistoryMetadataTool,
    GetChatHistoryEntryTool,
    DeleteChatHistoryEntriesTool,
    GetChatHistoryStatsTool,
    GetTodosTool,
    CreateTodoTool,
    UpdateTodoTool,
    DeleteTodoTool,
    ReadFolderContentTool,
    ReadFileContentTool,
    WriteFileContentTool,
    CreateFolderTool,
    RemovePathsTool,
    InsertTextInFileTool,
    ReplaceTextInFileTool,
    SearchInFileTool,
    CopyPathsTool,
    RenamePathTool,
    MovePathsTool,
    PathStatTool,
    CreateWordDocumentTool,
    RunTerminalCommandsTool,
    MultiXYPlotTool,
    WebSearchTool,
    ImageGenerationTool,
)

from chat_history import ChatHistoryManager
from tools.todo_tools import TodoManager

from secure_storage import load_config, save_config, get_secret, set_secret, delete_secret

import base64
from PIL import Image
import io
import json
import argparse
import config


def color_text(text, color_code):
    # Windows PowerShell supports ANSI escape codes in recent versions
    return f"\033[{color_code}m{text}\033[0m"


# Initialize global variables
chat_history_manager = None
todo_manager = None
agent = None
agent_name = config.AGENT_NAME
user_id = config.USER_ID
project_root = None
partial_images = {}

# Get API key (env takes precedence, fallback to credentials manager)
api_key = os.environ.get("OPENAI_API_KEY")
if not api_key:
    api_key = get_secret("api_token")

# Get Base URL (env takes precedence, fallback to credentials manager)
base_url = os.environ.get("OPENAI_API_BASE_URL")
if not base_url:
    cfg = load_config()
    base_url = cfg.get("base_url", "")


def initialize_agent(load_history=True):
    """Initialize the agent and managers."""
    global chat_history_manager, todo_manager, agent, project_root, partial_images, api_key, base_url
    
    chat_history_manager = ChatHistoryManager()
    todo_manager = TodoManager()
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    partial_images = {}
    
    # Load or clear history
    if load_history:
        chat_history_manager.load_history()
        chat_history_manager.load_generated_images()
    else:
        chat_history_manager.clear_history()
        chat_history_manager.clear_generated_images()
        todo_manager.clear_todos()
    
    # Initialize tools
    selected_tools = [
        # Memory Management Tools
        GetUserMemoriesTool(),
        CreateUserMemoryTool(),
        UpdateUserMemoryTool(),
        DeleteUserMemoryTool(),
        # Chat History Management Tools (optional - uncomment to enable)
        GetChatHistoryMetadataTool(),
        GetChatHistoryEntryTool(),
        DeleteChatHistoryEntriesTool(),
        GetChatHistoryStatsTool(),
        # To-do Management Tools
        GetTodosTool(),
        CreateTodoTool(),
        UpdateTodoTool(),
        DeleteTodoTool(),
        # File System Interaction Tools
        ReadFolderContentTool(root_path=project_root),
        ReadFileContentTool(root_path=project_root),
        WriteFileContentTool(root_path=project_root, permission_required=False),
        CreateFolderTool(root_path=project_root, permission_required=False),
        RemovePathsTool(root_path=project_root, permission_required=False),
        InsertTextInFileTool(root_path=project_root, permission_required=False),
        ReplaceTextInFileTool(root_path=project_root, permission_required=False),
        SearchInFileTool(root_path=project_root),
        CopyPathsTool(root_path=project_root),
        RenamePathTool(root_path=project_root),
        MovePathsTool(root_path=project_root),
        PathStatTool(root_path=project_root),
        # WebSearchTool(),
    ]
    
    # Initialize agent configuration
    agent_config = AgentConfig(
        model_name=config.MODEL_NAME,
        temperature=config.TEMPERATURE,
        reasoning={"effort": config.REASONING_EFFORT, "summary": config.REASONING_SUMMARY},
        text={"verbosity": config.TEXT_VERBOSITY},
        store=False,
        stream=True,
        tool_choice="auto",
        include=["reasoning.encrypted_content"],
    )
    
    agent = Agent(
        api_key=api_key,
        base_url=base_url,
        name=agent_name,
        tools=selected_tools,
        user_id=user_id,
        config=agent_config,
    )


def process_message(user_input_text, screenshots_b64=None, max_turns=None):
    """Process a message and return the event stream."""
    global agent, chat_history_manager

    if max_turns is None:
        max_turns = config.MAX_TURNS
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    formatted_input = f"> **Timestamp:** `{timestamp}`\nUser's input: {user_input_text}"
    
    if agent.client:
        # Start the agent run with text and optional screenshots
        stream = agent.run(
            message=formatted_input,
            input_messages=chat_history_manager.get_history(),
            max_turns=max_turns,
            screenshots_b64=screenshots_b64,  # Pass list of screenshots to agent
        )
        return stream
    else:
        return None


def handle_event(event, interactive_mode=True):
    """Handle an event - print to console if interactive, return for service mode."""
    global partial_images
    
    # Handle image saving for both modes
    if event["type"] == "response.image_generation_call.partial_image":
        b64_data = event['data'].partial_image_b64
        item_id = event['data'].item_id
        sequence_number = event['data'].sequence_number
        image_bytes = base64.b64decode(b64_data)
        image = Image.open(io.BytesIO(image_bytes))
        
        if item_id not in partial_images:
            partial_images[item_id] = {}
        partial_images[item_id][sequence_number] = image
        
        images_folder = os.path.join(project_root, "images")
        os.makedirs(images_folder, exist_ok=True)
        image_path = os.path.join(images_folder, f"{item_id}_partial_{sequence_number}.png")
        image.save(image_path, format="PNG")
        
        if interactive_mode:
            image.show(title=f"Partial Image {item_id}-{sequence_number}")
            print(color_text(f"Partial image saved to {image_path}", '32'), flush=True)
    
    elif event["type"] == "response.image_generation_call.completed":
        item_id = event['data'].item_id
        sequence_number = event['data'].sequence_number
        
        if item_id in partial_images and sequence_number in partial_images[item_id]:
            images_folder = os.path.join(project_root, "images")
            os.makedirs(images_folder, exist_ok=True)
            image_path = os.path.join(images_folder, f"{item_id}_completed_{sequence_number}.png")
            partial_images[item_id][sequence_number].save(image_path, format="PNG")
            
            if interactive_mode:
                print(color_text(f"Completed image saved to {image_path}", '32'), flush=True)
    
    elif event["type"] == "response.agent.done":
        # Reload history from file to respect any deletions/changes made during the run
        chat_history_manager.history = chat_history_manager.load_history()
        
        # Append only the NEW entries from this agent run
        chat_history_manager.append_entries(event["chat_history"])
        chat_history_manager.save_history()
        chat_history_manager.add_generated_images(event["generated_images"])
        chat_history_manager.save_generated_images()
        
        if interactive_mode:
            print(color_text("\n[Agent Done]", '32'), event.get("message", ""), 
                  f" (duration: {event.get('duration_seconds', 0)} seconds)", flush=True)
    
    # Print to console in interactive mode
    if interactive_mode:
        if event["type"] == "response.reasoning_summary_part.added":
            print(color_text("Thinking: ", '33'), end="", flush=True)
        elif event["type"] == "response.reasoning_summary_text.delta":
            print(event["delta"], end="", flush=True)
        elif event["type"] == "response.reasoning_summary_text.done":
            print("\n", flush=True)
        elif event["type"] == "response.content_part.added":
            print(color_text("Assistant: ", '36'), end="", flush=True)
        elif event["type"] == "response.output_text.delta":
            print(event["delta"], end="", flush=True)
        elif event["type"] == "response.output_text.done":
            print("\n", flush=True)
        elif event["type"] == "response.output_item.done":
            if event["item"].type == "function_call":
                print(color_text(f"\n[Function Call] {event['item'].name} with arguments: {event['item'].arguments}", '35'), flush=True)
            elif event["item"].type == "custom_tool_call":
                print(color_text(f"\n[Custom Tool Call] {event['item'].name} with arguments: {event['item'].input}", '35'), flush=True)
        elif event["type"] == "response.image_generation_call.generating":
            print(color_text(f"\n[Image Generation]...", '34'), flush=True)
        elif event["type"] == "response.image_generation_call.partial_image":
            print(color_text(f"\n[Image Generation] Partial Image {event['data'].partial_image_index}...", '34'), flush=True)
        elif event["type"] == "response.image_generation_call.completed":
            print(color_text(f"\n[Image Generation] Completed", '34'), flush=True)
        elif event["type"] == "response.completed":
            usage = event.get("usage", {})
            if usage:
                print(color_text(f"\n[Usage] {usage}", '34'), flush=True)


def run_interactive():
    """Run in interactive CLI mode."""
    # Ask about loading history
    while True:
        choice = input(color_text("\nDo you want to load previous chat history? (y/n): ", '35')).strip().lower()
        if choice in ['y', 'yes']:
            initialize_agent(load_history=True)
            break
        elif choice in ['n', 'no']:
            initialize_agent(load_history=False)
            break
        else:
            print(color_text("Please enter 'y' or 'n'.", '31'))
    
    print(color_text(f"Starting agent '{agent_name}' with user ID '{user_id}'...", '36'))
    
    while True:
        user_input = input(color_text("You: ", '35'))
        if user_input.lower() in ('exit', 'quit', 'q'):
            print(color_text("Exiting...", '31'))
            break
        
        if not user_input.strip():
            continue
        
        # Process message and handle events
        stream = process_message(user_input)

        if stream:
            for event in stream:
                handle_event(event, interactive_mode=True)


def run_service(port=None):
    """Run as FastAPI service."""
    if port is None:
        port = config.DEFAULT_PORT
    from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
    from pydantic import BaseModel
    import uvicorn
    
    app = FastAPI()
    
    # Initialize agent on startup with history loaded
    initialize_agent(load_history=True)

    print("Agent service initialized.")
    
    @app.get("/health")
    def health():
        return {"status": "ok", "service": config.SERVICE_NAME}
    
    @app.get("/chat/history")
    def get_chat_history():
        """Get the current chat history."""
        return {"history": chat_history_manager.get_history()}
    
    @app.delete("/chat/history")
    def clear_chat_history():
        """Clear the chat history."""
        try:
            chat_history_manager.clear_history()
            chat_history_manager.clear_generated_images()
            todo_manager.clear_todos()
            return {"status": "ok", "message": "Chat history cleared"}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
        
    @app.put("/settings/base_url")
    def update_base_url():
        """Signal that the base URL has been updated."""
        try:
            global base_url, agent
            cfg = load_config()
            existing_base_url = cfg.get("base_url", "")
            if existing_base_url:
                base_url = existing_base_url
            agent.update_client(base_url=base_url)
            print(f"Updated base URL to: {base_url}")
            return {"status": "ok", "message": "Base URL updated"}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
        
    @app.put("/settings/api_key")
    def update_api_key():
        """Signal that the API key has been updated."""
        try:
            global api_key, agent
            existing_token = get_secret("api_token")
            if existing_token:
                api_key = existing_token
            agent.update_client(api_key=api_key)
            print(f"Updated API key to: ****{existing_token[-4:]}")
            return {"status": "ok", "message": "API key updated"}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    @app.websocket("/chat/ws")
    async def chat_websocket(websocket: WebSocket):
        """WebSocket endpoint for streaming chat with chunked screenshot support."""
        await websocket.accept()
        
        # Track if we're currently processing a request
        processing = False
        
        try:
            while True:
                # Receive initial message from client
                data = await websocket.receive_json()
                msg_type = data.get("type", "message")
                
                # Handle stop request
                if msg_type == "stop":
                    if processing:
                        agent.stop()
                        await websocket.send_json({"type": "stop.acknowledged"})
                    continue
                
                # Handle different message types
                if msg_type == "message":
                    message = data.get("message", "")
                    has_screenshots = data.get("has_screenshots", False)
                    screenshot_count = data.get("screenshot_count", 0)
                    max_turns = data.get("max_turns", config.MAX_TURNS)
                    
                    screenshots_b64 = []
                    
                    # If screenshots are coming, receive them
                    if has_screenshots and screenshot_count > 0:
                        for _ in range(screenshot_count):
                            screenshot_msg = await websocket.receive_json()
                            if screenshot_msg.get("type") == "screenshot":
                                screenshots_b64.append(screenshot_msg.get("data"))
                        
                        # Wait for completion signal
                        complete_msg = await websocket.receive_json()
                        if complete_msg.get("type") != "screenshots_complete":
                            await websocket.send_json({"type": "error", "message": "Invalid screenshot sequence"})
                            continue
                    
                    if not message and not screenshots_b64:
                        await websocket.send_json({"type": "error", "message": "Empty message"})
                        continue
                    
                    try:
                        processing = True
                        # Process the message and stream events
                        # Use asyncio to avoid blocking the event loop
                        stream = process_message(message, screenshots_b64, max_turns)
                        
                        if stream:
                            # Iterate through events and yield control to event loop
                            for event in stream:
                                # Handle the event (saves history, images, etc.)
                                handle_event(event, interactive_mode=False)
                                
                                # Stream event to client
                                serialized_event = make_serializable(event)
                                await websocket.send_json(serialized_event)
                                
                                # Yield control to event loop to handle ping/pong
                                await asyncio.sleep(0)
                        
                        # Send completion signal
                        await websocket.send_json({"type": "stream.finished"})
                        processing = False
                        
                    except Exception as e:
                        processing = False
                        print(f"Error processing message: {e}")
                        import traceback
                        traceback.print_exc()
                        await websocket.send_json({
                            "type": "error",
                            "message": f"Error processing message: {str(e)}"
                        })
                        await websocket.send_json({"type": "stream.finished"})
                    
        except WebSocketDisconnect:
            print("WebSocket client disconnected")
        except Exception as e:
            print(f"WebSocket error: {e}")
            import traceback
            traceback.print_exc()
            try:
                await websocket.close()
            except:
                pass
    
    print(color_text(f"Starting agent service on port {port}...", '36'))
    print(color_text(f"API docs: http://localhost:{port}/docs", '33'))
    print(color_text(f"WebSocket endpoint: ws://localhost:{port}/chat/ws", '33'))
    
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AI Agent - Interactive or Service Mode")
    parser.add_argument("--mode", choices=["interactive", "service"], default="interactive",
                        help="Run mode (default: interactive)")
    parser.add_argument("--port", type=int, default=config.DEFAULT_PORT,
                        help=f"Port for service mode (default: {config.DEFAULT_PORT})")
    
    args = parser.parse_args()
    
    if args.mode == "interactive":
        run_interactive()
    else:
        run_service(args.port)
