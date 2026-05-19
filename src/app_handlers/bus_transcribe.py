"""Transcribe bus handler (extracted from src/app.py).

Move-first, refactor-later.
- Preserve bus topics.
- Preserve payload/response shapes.
- No UI imports.
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Dict, List


def register_transcribe_handlers(app: Any, bus: Any) -> List[Callable[[], None]]:
    """Register Transcribe bus handler. Returns unsubscribe callables."""
    return [bus.subscribe("transcribe.cmd.run", lambda ev: bus_transcribe_run(app, ev))]


def bus_transcribe_run(app: Any, event) -> None:
    payload = getattr(event, "payload", {}) or {}
    reply_topic = payload.get("reply_topic")
    language = payload.get("language", "en")
    audio_data = payload.get("audio_data")
    audio_path = payload.get("audio_path")

    if not reply_topic:
        return

    def work():
        try:
            data = audio_data
            if data is None and audio_path:
                with open(str(audio_path), "rb") as f:
                    data = f.read()

            if data is None:
                app._bus_reply(reply_topic, {"status": "error", "message": "audio_data (bytes) or audio_path is required"})
                return

            result = app.transcribe(audio_data=data, language=str(language or "en"))
            if result and isinstance(result, dict) and result.get("text"):
                app._bus_reply(reply_topic, {"status": "success", "text": result.get("text")})
            else:
                app._bus_reply(reply_topic, {"status": "error", "message": "Transcription failed"})
        except Exception as e:
            app._bus_reply(reply_topic, {"status": "error", "message": str(e)})

    threading.Thread(target=work, daemon=True).start()
