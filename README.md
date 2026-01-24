# AI Agent Desktop

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

A clean, modular monolith desktop AI assistant with voice input, chat interface, and powerful tools.

## 🚀 Quick Start

1. **Install dependencies:**
   ```bash
   setup.bat        # Windows
   pip install -r requirements.txt  # Or manually
   ```

2. **Run the agent:**
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
    └── components/     # Reusable UI parts
        ├── chat_window.py       # Chat interface
        ├── settings_window.py   # Settings dialog
        ├── multiline_input.py   # Text input widget
        ├── screenshot_selector.py # Screenshot tool
        └── chat_history_json_window.py # History viewer
```

## Features

### 🎤 Voice Input
- Long-press (1s) to record
- Auto-transcribe using OpenAI Whisper
- Multi-language support (en, ro, ru, de, fr, es)

### 💬 Chat Interface
- Type or speak your messages
- Real-time streaming responses
- File drag-and-drop attachment
- Screenshot sharing (up to 5)
- Syntax-highlighted code blocks with Pygments
- Encrypted, persistent history
- Token usage tracking
- Stop generation at any time

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

### Quick Start

1. **Set your API key:**
   - Right-click widget → Settings
   - Enter API token and base URL
   - Saved securely in OS keyring

2. **Customize:** Edit `config.yaml` and `prompts/system_prompt.md`

### Settings Structure

```yaml
# config.yaml
agent_name: Djasha

api:
  base_url: 'https://api.openai.com/v1'

agent:
  model_name: gpt-5.1
  system_prompt_path: prompts/system_prompt.md

ui:
  theme: dark

tools:
  enabled_tools: [memory, todos, filesystem, ...]
```

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
- Components are in `src/ui/components/`
- Main widget in `src/ui/widget.py`
- Styles are inline using PyQt6 stylesheets

## Storage and Security

- Chat history and memories are stored encrypted at:
  - `%APPDATA%/ai-agent-desktop/chat_history.enc`
  - `%APPDATA%/ai-agent-desktop/memories.enc`
- Encryption key (`data_key`) is stored in Windows Credential Manager:
  - Service: `ai-agent-desktop/data_key`, Username: `data_key`
- API Token saved from Settings is stored under:
  - Service: `ai-agent-desktop/api_token`, Username: `api_token`
- See `secure_storage/README.md` for details.

## Contributing

Contributions are welcome! Feel free to:
- Report bugs or issues
- Suggest new features
- Submit pull requests
- Improve documentation

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Author

**destorted93**
- GitHub: [@destorted93](https://github.com/destorted93)
- Repository: [ai-agent-desktop](https://github.com/destorted93/ai-agent-desktop)
