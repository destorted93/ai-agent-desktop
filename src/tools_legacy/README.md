# Tools Collection

The agent's capabilities - everything it can actually do.

## Available Tools

### Memory Tools
- Manage persistent user memories and context

### Chat History Tools
- Query and manage conversation history

### Todo Tools
- Create, update, and track tasks
- **Storage**: `todos.json`

### Filesystem Tools
- **Read**: Files and folders
- **Write**: Create and modify files
- **Search**: Find content in files
- **Manage**: Copy, move, rename, delete paths
- **Edit**: Insert and replace text with line/column precision

### Web & Media Tools
- **Web Search**: Search and scrape web content
- **Image Generation**: Create images using AI

### Document Tools
- Create formatted Word documents (`.docx`)

### DevOps Tools
- Run terminal commands from the agent

### Visualization Tools
- Generate multi-series XY plots and charts

## Tool Structure

Each tool implements:
```python
class Tool:
    schema = { ... }  # JSON schema defining the tool
    def run(self, **params):  # Execute the tool
        return { ... }
```

## Adding New Tools

1. Create tool class with `schema` and `run()` method
2. Import in `tools/__init__.py`
3. Add to agent's tool list in `agent-main/app.py`

## Integration

All tools are loaded by `agent-main/app.py` and passed to the Agent instance
