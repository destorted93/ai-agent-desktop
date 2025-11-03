# Memory Service

Simple persistent memory system for storing user context and preferences.

## What it does

Stores key facts about the user that the AI agent should remember across sessions (name, preferences, context, etc.).

## Storage

- Location: `%APPDATA%/ai-agent-desktop/memories.enc`
- Format: Encrypted JSON (Fernet AEAD)
- Key management: Encryption key (`data_key`) is stored in Windows Credential Manager under service `ai-agent-desktop/data_key` (username `data_key`).

The module uses the shared `secure_storage` package to read/write encrypted data. No plaintext memory files are used.

## Memory Structure

```json
{
  "id": "1",
  "date": "2025-10-09",
  "time": "14:30:00",
  "text": "User prefers Python over JavaScript"
}
```

## Usage

The agent automatically uses memory tools to:
- Retrieve memories at conversation start
- Add new memories during conversations
- Update existing memories
- Delete outdated memories

Programmatic access:

```python
from memory.memory import MemoryManager

mm = MemoryManager()
all_memories = mm.get_memories()
mm.add_memory("User prefers Python over JavaScript")
```

## Tools Available

- `get_user_memories` - Fetch all stored memories
- `create_user_memory` - Add a new memory
- `update_user_memory` - Modify existing memory by ID
- `delete_user_memory` - Remove memory by ID

## Integration

Imported and used by `agent-main/app.py` through the tools system.
