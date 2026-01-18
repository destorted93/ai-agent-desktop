import os
import uuid

class RunTerminalCommandsTool:
    schema = {
        "type": "function",
        "name": "run_terminal_commands",
        "description": (
            "Run one or more terminal commands in the Windows environment, starting from the project root directory. "
            "Provide a list of commands to execute. The tool will concatenate them using '&&' and run them as a single command. "
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "commands": {"type": "array", "items": {"type": "string"}, "description": "A list of terminal commands to run from the project root directory."}
            },
            "required": ["commands"],
            "additionalProperties": False,
        },
    }

    def __init__(self, root_path, permission_required=True):
        self.root_path = root_path
        self.permission_required = permission_required

    def run(self, commands):
        if not isinstance(commands, list) or not commands:
            print("No commands provided.")
            return {"status": "error", "message": "No commands provided."}
        command_str = " && ".join(commands)
        if self.permission_required:
            permission = input(f"Run the following command(s) from project root?\n{command_str}\nProceed? (y/n): ")
            if permission.lower() != 'y':
                print("Command execution cancelled by user.")
                return {"status": "error", "message": "Command execution cancelled by user."}
        try:
            import subprocess
            print(f"Executing: {command_str}")
            result = subprocess.run(command_str, shell=True, cwd=self.root_path, capture_output=True, text=True)
            print(f"Return code: {result.returncode}")
            print(f"Output:\n{result.stdout}")
            if result.stderr:
                print(f"Error:\n{result.stderr}")
            return {
                "status": "success" if result.returncode == 0 else "error",
                "output": result.stdout,
                "error": result.stderr,
                "returncode": result.returncode,
            }
        except Exception as e:
            print(f"Exception: {str(e)}")
            return {"status": "error", "message": str(e)}
