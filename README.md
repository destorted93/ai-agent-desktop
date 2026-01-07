# AI Agent

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Desktop AI assistant with voice input, chat interface, encrypted history, and powerful tools.

## 🚀 Quick Start

**Double-click `START_CLEAN.bat`** to launch everything:
- 🎤 Transcribe Service (voice-to-text)
- 🤖 Agent Service (AI brain)
- 💬 Widget (desktop interface)

Everything runs in the background - no terminal windows! The widget appears on your desktop ready to use.

## What it does

A modular AI agent that:
- Listens to your voice and transcribes it
- Chats with you using GPT-5
- Executes tasks through tools
- Remembers context across sessions
- Runs as separate services for stability

## Architecture

**Multi-service architecture** - Each component runs independently:

### Core Services
- **agent-main/** - Main AI agent (CLI + API modes)
- **agent/** - Agent core (OpenAI wrapper with streaming)
- **transcribe/** - Audio → text conversion
- **widget/** - Desktop UI with voice/chat

### Data & Tools
- **chat_history/** - Conversation persistence (encrypted)
- **memory/** - User context storage (encrypted)
- **secure_storage/** - Config, keychain secrets, and encrypted JSON helpers
- **tools/** - Agent capabilities (filesystem, web, todos, etc.)

### Utilities
- **service-template/** - Boilerplate for new services

## Features

### 🎤 Voice Input
- Click to record
- Auto-transcribe using Whisper
- Multi-language support

### 💬 Chat Interface
- Type or speak your messages
- Real-time streaming responses
- Color-coded output (thinking, responses, function calls)
- Screenshot sharing
- Encrypted, persistent history

### 🛠️ Agent Capabilities
- **Files**: Read, write, search, edit
- **Web**: Search and scrape
- **Todos**: Task management
- **Memory**: Remember user preferences
- **Documents**: Create Word files
- **Charts**: Generate visualizations
- **Terminal**: Run commands
- **Images**: AI image generation

## Installation

### Option 1: Quick Install
```bash
INSTALL.bat
```

### Option 2: Manual Install
```bash
pip install -r agent-main/requirements.txt
pip install -r transcribe/requirements.txt
pip install -r widget/requirements.txt
```

## Running

### Complete System (Recommended)

**1. Install dependencies first:**
```bash
INSTALL.bat
```

**2. Launch the agent:**
```bash
START_CLEAN.bat  # Clean launch - runs in background, no terminals
```

This is the main way to use the agent. Just close the widget to stop everything.

### Alternative Launchers
```bash
START.bat        # Shows terminal windows (useful for debugging)
```

### Individual Components
```bash
# Interactive CLI
python agent-main/app.py --mode interactive

# Agent API
python agent-main/app.py --mode service --port 6002

# Transcribe service
python transcribe/app.py

# Widget only
python widget/widget.py
```

## Configuration

Set your OpenAI API key (either method works):
```bash
# Windows
$env:OPENAI_API_KEY = "sk-..."

# Or use the widget Settings (⚙) to save an API Token
# The token is stored in Windows Credential Manager under
#   Service: ai-agent-desktop/api_token, Username: api_token
```

## Service Ports

- **6001** - Transcribe service
- **6002** - Agent service
- Widget connects to both

## Adding Features

Each service has its own README with details:
- `/agent-main/README.md` - Main agent docs
- `/transcribe/README.md` - Transcription service
- `/widget/README.md` - Desktop widget
- `/tools/README.md` - Available tools

## Project Layout

```
ai-agent-desktop/
├── INSTALL.bat         # Install all dependencies
├── START_CLEAN.bat     # Launch everything (background, no terminals)
├── START.bat           # Launch with visible terminals
├── agent-main/         # Main AI agent
├── agent/              # Core agent logic
├── transcribe/         # Voice-to-text service
├── widget/             # Desktop interface
├── tools/              # Agent tools
├── chat_history/       # Conversation storage
├── memory/             # User context
├── secure_storage/     # Shared secure storage helpers
└── service-template/   # New service boilerplate
```

## Why Multi-Service Architecture?

- **Isolation**: One crash doesn't kill everything
- **Resources**: Distribute load across processes
- **Development**: Work on parts independently
- **Scaling**: Add more services easily

Set your OpenAI API key:
```powershell
# PowerShell (permanent)
[System.Environment]::SetEnvironmentVariable('OPENAI_API_KEY', 'sk-...', 'User')

# Or just enter it when prompted by START.bat
```

## Usage Guide

### Getting Started
1. **Install**: Run `INSTALL.bat` to install all dependencies
2. **Launch**: Double-click `START.bat` to start all services
3. **Use**: Click 💬 on the widget to open chat, or use voice recording

For detailed instructions, see [LAUNCH_GUIDE.md](LAUNCH_GUIDE.md)

### Widget Controls
- **▶ Start Recording** - Record voice input
- **⏹ Stop Recording** - Stop and transcribe
- **💬 Chat** - Open/close chat window
- **⚙ Settings** - Language selection, Base URL, and API Token

### Chat Window
- Type messages or use voice input
- Real-time streaming responses
- Persistent chat history
- Color-coded display for different response types

For detailed chat features, see [widget/CHAT_FEATURE.md](widget/CHAT_FEATURE.md)

## Documentation

- **[QUICKSTART.md](QUICKSTART.md)** - Quick start guide
- **[LAUNCH_GUIDE.md](LAUNCH_GUIDE.md)** - Detailed launch instructions
- **[COMPLETE_SETUP.md](COMPLETE_SETUP.md)** - Complete setup summary
- **[widget/CHAT_FEATURE.md](widget/CHAT_FEATURE.md)** - Chat feature documentation
- **[CHAT_IMPLEMENTATION_SUMMARY.md](CHAT_IMPLEMENTATION_SUMMARY.md)** - Technical details

## API Documentation

When services are running, access interactive API docs:
- **Transcribe Service**: http://localhost:6001/docs
- **Agent Service**: http://localhost:6002/docs

## Services Overview

### Transcribe Service (Port 6001)
- Audio transcription using OpenAI Whisper
- Multi-language support
- FastAPI-based REST API

### Agent Service (Port 6002)
- Conversational AI with GPT-5
- Tool execution (file ops, web search, memory, todos)
- Streaming responses
- Chat history management

### Widget (Desktop App)
- Always-on-top interface
- Voice recording with transcription
- Chat window with encrypted, persistent history
- Draggable, customizable position

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
