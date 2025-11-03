"""
Launcher for AI Agent - Runs all services in background and manages lifecycle
"""
import os
import sys
import subprocess
import time
import signal
import atexit
import requests

# Store process references
processes = []

def cleanup_processes():
    """Kill all child processes on exit."""
    print("Shutting down services...")
    for proc in processes:
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except:
            try:
                proc.kill()
            except:
                pass
    print("All services stopped.")

# Register cleanup on exit
atexit.register(cleanup_processes)

def signal_handler(sig, frame):
    """Handle Ctrl+C gracefully."""
    print("\nReceived interrupt signal...")
    cleanup_processes()
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

def start_service(name, cmd, cwd, env_vars=None, wait_for_health=None):
    """Start a service in the background."""
    print(f"[{name}] Starting...")
    
    # Prepare environment
    env = os.environ.copy()
    if env_vars:
        env.update(env_vars)
    
    # Start process with hidden window on Windows
    if sys.platform == 'win32':
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            env=env,
            startupinfo=startupinfo,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
    else:
        proc = subprocess.Popen(cmd, cwd=cwd, env=env)
    
    processes.append(proc)
    
    # Wait for health check if provided
    if wait_for_health:
        max_attempts = 30
        for i in range(max_attempts):
            try:
                response = requests.get(wait_for_health, timeout=1)
                if response.status_code == 200:
                    print(f"[{name}] Ready!")
                    return proc
            except:
                pass
            time.sleep(1)
        print(f"[{name}] Started (health check timeout)")
    else:
        time.sleep(2)
        print(f"[{name}] Started")
    
    return proc

def main():
    """Main launcher function."""
    # Get API key
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        # Show error dialog if running hidden
        if sys.platform == 'win32':
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                0,
                "OPENAI_API_KEY environment variable is not set.\n\n"
                "Please set it in your system environment variables or run START.bat instead.",
                "API Key Missing",
                0x10  # MB_ICONERROR
            )
        else:
            print("Error: OPENAI_API_KEY environment variable is not set!")
        sys.exit(1)
    
    script_dir = os.path.dirname(os.path.abspath(__file__))
    transcribe_dir = os.path.join(script_dir, "transcribe")
    agent_dir = os.path.join(script_dir, "agent-main")
    widget_dir = os.path.join(script_dir, "widget")
    
    transcribe_port = 6001
    agent_port = 6002
    
    print("\n" + "=" * 50)
    print("  Starting AI Agent System")
    print("=" * 50 + "\n")
    
    # Start transcribe service
    start_service(
        "Transcribe Service",
        [sys.executable, "app.py"],
        transcribe_dir,
        env_vars={
            "OPENAI_API_KEY": api_key,
            "PORT": str(transcribe_port)
        },
        wait_for_health=f"http://127.0.0.1:{transcribe_port}/health"
    )
    
    # Start agent service
    start_service(
        "Agent Service",
        [sys.executable, "app.py", "--mode", "service", "--port", str(agent_port)],
        agent_dir,
        env_vars={"OPENAI_API_KEY": api_key},
        wait_for_health=f"http://127.0.0.1:{agent_port}/health"
    )
    
    print("\n" + "=" * 50)
    print("  All Services Ready!")
    print("=" * 50)
    print(f"  - Transcribe: http://localhost:{transcribe_port}")
    print(f"  - Agent:      http://localhost:{agent_port}")
    print("=" * 50 + "\n")
    
    print("Starting Widget UI...")
    
    # Start widget (foreground - this is the main UI)
    # Ensure the repo root is importable for shared modules (e.g., secure_storage)
    repo_root = script_dir
    existing_pp = os.environ.get("PYTHONPATH", "")
    new_pp = repo_root if not existing_pp else repo_root + os.pathsep + existing_pp

    widget_proc = subprocess.Popen(
        [sys.executable, "widget.py"],
        cwd=widget_dir,
        env={
            **os.environ,
            "PYTHONPATH": new_pp,
            "TRANSCRIBE_URL": f"http://127.0.0.1:{transcribe_port}/upload",
            "AGENT_URL": f"http://127.0.0.1:{agent_port}"
        }
    )
    
    # Wait for widget to close
    widget_proc.wait()
    
    print("\nWidget closed. Shutting down services...")
    cleanup_processes()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nShutdown requested...")
        cleanup_processes()
    except Exception as e:
        print(f"Error: {e}")
        cleanup_processes()
        sys.exit(1)
