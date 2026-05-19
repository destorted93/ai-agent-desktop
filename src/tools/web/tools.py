"""Web and media tools."""

from typing import Dict, Any


class WebSearchTool:
    """Web search tool (uses OpenAI's built-in web search)."""
    
    def __init__(self):
        self.schema = {
            "type": "web_search",
            "name": "web_search",
            }
    
    def run(self, query: str = "") -> Dict[str, Any]:
        # This is handled by the OpenAI API directly
        return {"status": "success"}
