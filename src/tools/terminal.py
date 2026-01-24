"""Terminal command execution tool."""

import os
import subprocess
from typing import List, Dict, Any


class RunTerminalTool:
    """Tool to run terminal commands."""
    
    def __init__(self, root_path: str, permission_required: bool = False):
        self.root_path = root_path
        self.permission_required = permission_required
        self.schema = {
            "type": "function",
            "name": "run_terminal",
            "description": (
                "Run one or more terminal commands from the project root directory. "
                "Commands are concatenated with '&&' and executed as a single command."
            ),
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "commands": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of commands to execute.",
                    },
                },
                "required": ["commands"],
                "additionalProperties": False,
            },
        }
    
    def run(self, commands: List[str]) -> Dict[str, Any]:
        if not commands:
            return {"status": "error", "message": "No commands provided"}
        
        command_str = " && ".join(commands)
        
        try:
            result = subprocess.run(
                command_str,
                shell=True,
                cwd=self.root_path,
                capture_output=True,
                text=True,
                timeout=120,  # 2 minute timeout
            )
            
            return {
                "status": "success" if result.returncode == 0 else "error",
                "output": result.stdout,
                "error": result.stderr,
                "returncode": result.returncode,
            }
        except subprocess.TimeoutExpired:
            return {"status": "error", "message": "Command timed out (120s limit)"}
        except Exception as e:
            return {"status": "error", "message": str(e)}
