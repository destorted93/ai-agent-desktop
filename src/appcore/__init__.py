"""appcore: the app substrate.

This package holds foundational systems.

Important import hygiene:
- Keep this __init__ lightweight.
- Avoid importing modules that transitively import src.storage at import-time.
  (Otherwise, storage modules cannot safely import appcore submodules like
  appcore.run_context without circular-import risk.)

If you need Runtime/ConfigManager, import them directly:
- from src.appcore.runtime_context import Runtime
- from src.appcore.config_manager import ConfigManager
"""

__all__ = []
