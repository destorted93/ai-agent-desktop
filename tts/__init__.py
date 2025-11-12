"""Text-to-Speech module.

Provides a small wrapper around the OpenAI Text-to-Speech API that can be
imported and used by tools under the `tools` directory (or anywhere else).

Exports:
    - TTSConfig: Lightweight configuration container
    - TTSClient:  Simple client for generating speech to a file or bytes
"""

from .tts import TTSClient, TTSConfig

__all__ = [
    "TTSClient",
    "TTSConfig",
]

