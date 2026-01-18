# AI Agent Desktop

A clean, modular monolith desktop AI assistant with voice input, chat interface, and powerful tools.

## 🚀 Quick Start

1. **Install dependencies:**
   ```bash
   setup.bat        # Windows
   pip install -r requirements.txt  # Or manually
   ```

2. **Set your API key:**
   ```bash
   set OPENAI_API_KEY=your-key-here
   ```

3. **Run the agent:**
   ```bash
   run.bat          # Windows
   python run.py    # Or directly
   ```

## Architecture

This is a **modular monolith** - a single-process application with clean module boundaries:

```
src/
├── app.py              # Main application entry point
├── config/             # Configuration management
│   ├── settings.py     # YAML-based settings with Pydantic
│   ├── agent_config.py # Agent API parameters
│   └── prompts.py      # System prompts
├── core/               # Core agent logic
│   └── agent.py        # AI agent with streaming & tools
├── storage/            # Data persistence
│   ├── secure.py       # Encrypted storage (keyring)
│   ├── chat_history.py # Conversation persistence
│   └── memory.py       # User memories
├── tools/              # Agent capabilities
│   ├── memory.py       # Memory management
│   ├── todos.py        # Task management
│   ├── filesystem.py   # File operations
│   ├── terminal.py     # Command execution
│   └── ...             # More tools
├── services/           # In-process services
│   ├── transcribe.py   # Voice-to-text
│   └── tts.py          # Text-to-speech
└── ui/                 # PyQt6 interface
    ├── widget.py       # Floating widget
    ├── chat_window.py  # Chat interface
    └── components/     # Reusable UI parts
```

## Features

### 🎤 Voice Input
- Click to record
- Auto-transcribe using OpenAI Whisper
- Multi-language support

### 💬 Chat Interface
- Type or speak your messages
- Real-time streaming responses
- Screenshot sharing (up to 5)
- Syntax-highlighted code blocks
- Encrypted, persistent history

### 🛠️ Agent Tools
- **Memory**: Remember user preferences
- **Todos**: Task management
- **Files**: Read, write, search, edit
- **Terminal**: Run commands
- **Documents**: Create Word files
- **Charts**: Generate visualizations
- **Web**: Search and browse
- **Images**: AI image generation

## Configuration

Copy `config.example.yaml` to `config.yaml` and customize:

```yaml
# Agent identity
agent_name: Atlas

# Agent behavior
agent:
  model_name: gpt-4o
  temperature: 1.0
  max_turns: 16

# UI settings
ui:
  theme: dark
  always_on_top: true

# Tool settings
tools:
  terminal_permission_required: false
```

### Environment Variables

- `OPENAI_API_KEY` - Your OpenAI API key (required)
- `OPENAI_BASE_URL` - Custom API endpoint (optional)

## Key Improvements Over Previous Version

1. **Single Process**: No more WebSockets, HTTP, or inter-process communication
2. **Direct Imports**: Modules communicate via Python imports, not network calls
3. **Clean Separation**: Each module has a single responsibility
4. **Easy Configuration**: YAML config file + environment variables
5. **Type Safety**: Pydantic models for configuration validation
6. **Simpler Deployment**: Just `python run.py`

## Development

### Project Structure
- `src/` - All application code
- `config.yaml` - User configuration
- `requirements.txt` - Python dependencies

### Adding New Tools
1. Create a new file in `src/tools/`
2. Define a class with `schema` property and `run()` method
3. Register in `src/tools/__init__.py`
4. Add to `get_default_tools()` function

### Customizing the UI
- Styles are centralized in `src/ui/styles.py`
- Components are in `src/ui/components/`
- Main windows in `src/ui/widget.py` and `src/ui/chat_window.py`

## License

MIT License
