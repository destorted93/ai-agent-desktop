# Chat History Service

Manages conversation history with the AI agent across sessions.

## What it does

Persists entire conversation threads including:
- User messages
- Agent responses
- Function calls and outputs
- Generated images
- Reasoning traces

## Storage

- Chat History: `%APPDATA%/ai-agent-desktop/chat_history.enc` (encrypted JSON via Fernet)
- Generated Images: `generated_images.json` (metadata) — unchanged

Encryption key management:
- The symmetric key (`data_key`) is stored in Windows Credential Manager under service `ai-agent-desktop/data_key` (username `data_key`).
- Encryption/decryption is handled by the shared `secure_storage` package.

## Entry Format

Each entry is wrapped with metadata:

```json
{
  "id": "uuid",
  "ts": "2025-10-09T14:30:00",
  "type": "text",
  "size": 1234,
  "content": { ... }
}
```

## Features

- Auto-load: Previous conversations resume automatically (from encrypted store)
- Searchable: Query history by metadata or content
- Stats: Track conversation size and entry counts
- Cleanup: Delete old or unwanted entries

## Tools Available

- `get_chat_history_metadata` - List all entries with metadata
- `get_chat_history_entry` - Retrieve specific entry by ID
- `delete_chat_history_entries` - Remove entries by ID
- `get_chat_history_stats` - Get conversation statistics

## Integration

Used by `agent-main/app.py` to maintain conversation context.
3. Management: Selectively delete or analyze entries by ID
4. Analytics: Track conversation size, types, and growth over time

## New Tools

Four new tools are available for managing chat history:

### 1. GetChatHistoryMetadataTool

Retrieve metadata about all entries without loading full content:

```python
GetChatHistoryMetadataTool()
```

Returns: List of entries with `id`, `ts`, `type`, `size` only.

**Use cases:**
- Analyze conversation flow
- Identify large entries
- Find specific messages to inspect or delete

### 2. GetChatHistoryEntryTool

Get the full wrapped entry (including content) by ID:

```python
GetChatHistoryEntryTool()
```

**Use cases:**
- Inspect specific message content
- Review entries before deletion
- Debug conversation issues

### 3. DeleteChatHistoryEntriesTool

Delete entries by their IDs, or delete ALL entries at once:

```python
# Delete all entries (efficient)
DeleteChatHistoryEntriesTool(delete_all=True)

# Delete specific entries by ID
DeleteChatHistoryEntriesTool(entry_ids=['id1', 'id2'])
```

**Two modes:**
1. **Delete All**: Set `delete_all=true` to clear entire history in 1 call (no metadata needed)
2. **Delete Specific**: Provide `entry_ids` to delete selected entries

**When to use each:**
- "Delete all", "clear history" → Use `delete_all=true` (efficient!)
- "Delete reasoning entries", "delete messages from today" → Use `entry_ids`

**⚠️ Use with EXTREME CAUTION:**
- Deleting entries can break conversation context
- Only use when user explicitly requests deletion
- Best for removing errors, redundant, or sensitive entries

### 4. GetChatHistoryStatsTool

Get statistical overview of chat history:

```python
GetChatHistoryStatsTool()
```

Returns:
- Total entries and size
- Breakdown by entry type
- Oldest and newest timestamps
- Size distribution

**Use cases:**
- Monitor conversation growth
- Identify optimization opportunities
- Understand conversation composition

## Enabling History Tools

By default, history management tools are commented out in `app.py`. To enable them:

```python
# In app.py, in initialize_agent() function:
selected_tools = [
    # ... other tools ...
    
    # Uncomment these lines:
    GetChatHistoryMetadataTool(),
    GetChatHistoryEntryTool(),
    DeleteChatHistoryEntriesTool(),
    GetChatHistoryStatsTool(),
    
    # ... more tools ...
]
```

## API Changes

### ChatHistoryManager Methods

**New methods:**
- `get_wrapped_history()` - Returns full wrapped entries with metadata
- `delete_entries_by_ids(entry_ids)` - Delete entries by ID
- `get_entry_by_id(entry_id)` - Get single entry by ID

**Modified methods:**
- `get_history()` - Still returns OpenAI-compatible message list (unwrapped)
- `add_entry(entry)` - Now wraps entry and returns its ID
- `append_entries(entries)` - Now wraps entries and returns list of IDs

**No changes needed** in most code - `get_history()` still returns the same format OpenAI expects!

## Example Usage

### Get conversation statistics

```python
from tools import GetChatHistoryStatsTool

tool = GetChatHistoryStatsTool()
stats = tool.run()

print(f"Total entries: {stats['total_entries']}")
print(f"Total size: {stats['total_size_kb']} KB")
print(f"By type: {stats['stats_by_type']}")
```

### List all entries

```python
from tools import GetChatHistoryMetadataTool

tool = GetChatHistoryMetadataTool()
result = tool.run()

for entry in result['entries']:
    print(f"{entry['ts']} - {entry['type']} - {entry['size']} bytes - ID: {entry['id']}")
```

### Delete all entries (efficient)

```python
from tools import DeleteChatHistoryEntriesTool

tool = DeleteChatHistoryEntriesTool()
result = tool.run(delete_all=True)

print(f"Deleted {result['deleted_count']} entries")
print(f"Remaining: {result['remaining_count']} entries")
print(f"Message: {result['message']}")
```

### Delete specific entries

```python
from tools import DeleteChatHistoryEntriesTool, GetChatHistoryMetadataTool

# First, get metadata to find entries
metadata_tool = GetChatHistoryMetadataTool()
metadata = metadata_tool.run()

# Filter for entries you want to delete (e.g., reasoning entries)
ids_to_delete = [e['id'] for e in metadata['entries'] if e['type'] == 'reasoning']

# Delete them
delete_tool = DeleteChatHistoryEntriesTool()
result = delete_tool.run(entry_ids=ids_to_delete)

print(f"Deleted {result['deleted_count']} entries")
print(f"Remaining: {result['remaining_count']} entries")
```

## Best Practices

1. **Enable tools only when needed** - History management tools are powerful; enable only for administrative tasks
2. **Back up before bulk deletions** - The migration script creates backups; consider doing the same before manual deletions
3. **Use stats tool first** - Understand your conversation before making changes
4. **Review before deleting** - Use `GetChatHistoryEntryTool` to inspect entries before deletion
5. **Monitor size growth** - Use stats tool periodically to track conversation size

## Technical Details

- Data is encrypted using Fernet (AES-128 + HMAC) via the `secure_storage` helpers.
- The encryption key is stored in the Windows Credential Manager (`ai-agent-desktop/data_key`).
- No legacy JSON file is used for chat history. All reads/writes go to the encrypted store.

### Wrapping Process

When an entry is added:
1. Calculate JSON size in bytes
2. Generate unique UUID
3. Create ISO 8601 timestamp
4. Determine entry type from content
5. Wrap in metadata envelope
6. Save to file

### Unwrapping Process

When history is requested for OpenAI:
1. Load wrapped entries from file
2. Extract `content` field from each entry
3. Return list of unwrapped content objects
4. OpenAI receives standard message format

This design ensures **zero impact** on OpenAI API interactions while enabling powerful management capabilities.
